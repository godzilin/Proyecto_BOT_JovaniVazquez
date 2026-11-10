// Cliente de las rutas del editor del mundo (`/api/editor/*`, solo con permiso de editor): lanza el texto de error del servidor tal cual.
import { FORMATO } from "./formato";

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

/** Otra persona con el editor ha guardado antes (409): hay que recargar. */
export class Conflicto extends Error {}

/** Un trozo del borrador tal como lo guarda el servidor: `doc` es `null` en un trozo borrado, que conserva su `rev`. */
export interface TrozoBorrador {
  doc: unknown;
  rev: number;
}

/** `cuerpo` que ya es un texto JSON (`guardar` pega los trozos tal cual) se manda sin volver a codificar. */
async function llamar<T>(s: Sesion, metodo: string, ruta: string, cuerpo?: unknown, crudo?: ArrayBuffer): Promise<T> {
  const headers: Record<string, string> = { Authorization: `Bearer ${s.session}` };
  let body: BodyInit | undefined;
  if (crudo) body = crudo;
  else if (cuerpo !== undefined) {
    headers["Content-Type"] = "application/json";
    body = typeof cuerpo === "string" ? cuerpo : JSON.stringify(cuerpo);
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

/** El borrador sin las celdas: el `rev` de cada trozo (las lápidas aparte) y los documentos de lo que no es celda. */
export interface Indice {
  version: number;
  trozos: Record<string, number>;
  borrados: Record<string, number>;
  docs: Record<string, unknown>;
}
/** Hasta `MAX_LOTE` trozos por petición, como los lotes del juego (`cercania.ts`). */
export const MAX_LOTE = 64;

export const indice = (s: Sesion) => llamar<Indice>(s, "GET", `indice${q(s)}`);
/** Unos trozos del borrador (hasta `MAX_LOTE`); una clave que no existe sale con `doc` nulo y `rev` 0. */
export const trozos = (s: Sesion, claves: string[]) =>
  llamar<{ trozos: Record<string, TrozoBorrador> }>(s, "GET", `trozos${q(s)}&t=${encodeURIComponent(claves.join(","))}`);
/** Las celdas del borrador que usan un asset, bajadas o no. */
export const usos = (s: Sesion, asset: string) => llamar<{ celdas: string[] }>(s, "GET", `usos${q(s)}&asset=${encodeURIComponent(asset)}`);
/** Guarda unos trozos (clave, `rev` que se cree, texto JSON o `null` para borrarlo), todos o ninguno. Devuelve el `rev` nuevo de cada uno. */
export const guardar = (s: Sesion, trozos: [string, number, string | null][]) =>
  llamar<{ revs: Record<string, number> }>(
    s,
    "PUT",
    "borrador",
    `{"guild_id":${JSON.stringify(s.guildId)},"v":${FORMATO},"trozos":{${trozos.map(([k, rev, doc]) => `${JSON.stringify(k)}:{"rev":${rev},"doc":${doc ?? "null"}}`).join(",")}}}`,
  );
export const publicar = (s: Sesion) => llamar<{ version: number }>(s, "POST", "publicar", { guild_id: s.guildId });
export const versiones = (s: Sesion) => llamar<{ versiones: Version[] }>(s, "GET", `versiones${q(s)}`);
export const volver = (s: Sesion, version: number) => llamar<Record<string, never>>(s, "POST", "volver", { guild_id: s.guildId, version });
export const subirGlb = (s: Sesion, datos: ArrayBuffer) =>
  llamar<{ sha256: string; triangles: number }>(s, "POST", `glb${q(s)}`, undefined, datos);
