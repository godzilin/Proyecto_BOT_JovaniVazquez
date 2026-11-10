// Las puertas y los interiores del mundo (fase 4 de `docs/mundo-celdas.md`). Una puerta es un objeto del editor cuyo asset tiene la
// función `puerta` y que lleva en `datos.interior` el id de un interior (trozo `i/<id>`). Al acercarse a una (`PRECARGA`) se baja su
// interior y los assets que usa, de la misma fuente que el mundo; al alejarse (`SUELTA`) se olvida. Con E (o tocando el aviso) se
// pide entrar a `/api/interior`: el servidor mira que estés en la puerta, decide en qué lugar entras (tu piso o el de todos) y te
// pasa a su capa de la sala, así que dentro solo ves a los que están en el mismo sitio y los de fuera no te ven. La página monta la
// habitación en el sitio de la puerta (`montaje.ts`), la cámara deja de ver el mundo de fuera (`mundo/capas.ts`), el suelo y las
// paredes pasan a ser los de la habitación (`mundo/dentro.ts`) y solo chocan sus muebles. Dentro, E usa lo que tengas cerca (lo
// decide y lo guarda el servidor, que avisa a los demás de dentro con `{t: "uso"}`) o sale por la puerta.
//
// Sin sesión (desarrollo fuera de Discord) se entra y se usa sin servidor, sin guardar nada.
import { Color } from "three";
import type { Asset, Interior, MundoDoc } from "../editor/formato";
import { claveCelda } from "../editor/trozos";
import type { Mundo } from "../mundo";
import { CAPA_INTERIOR } from "../mundo/capas";
import { soloEstos } from "../mundo/colision";
import { fijarCaja } from "../mundo/dentro";
import { PRUEBAS } from "../mundo/modo";
import type { Sesion } from "../paseo";
import { type Montado, montar, type UsableMontado } from "./montaje";

/** Metros a una puerta desde los que se baja su interior, y desde los que se olvida lo bajado. */
const PRECARGA = 40;
const SUELTA = 90;
/** Metros a la puerta para entrar (el servidor deja hasta 4, por el retraso de la pose), a un usable y a la salida. */
const ALCANCE = 2.2;
const ALCANCE_USO = 1.8;
const ALCANCE_SALIDA = 1.6;
/** Trozos por petición (`MAX_LOTE` del servidor). */
const LOTE = 64;
/** El fondo de la escena dentro: no se ve el cielo de fuera por encima de las paredes. */
const FONDO_DENTRO = new Color(0x1d1a17);

interface Puerta {
  interior: string;
  x: number;
  y: number;
  z: number;
  yaw: number;
}

interface Bajado {
  interior: Interior;
  assets: Record<string, Asset>;
  x: number;
  z: number;
}

interface Dentro {
  puerta: Puerta;
  m: Montado;
  puedes: boolean;
}

interface RespuestaEntrar {
  lugar: string;
  propio: boolean;
  piloto: boolean;
  puedes: boolean;
  estados: Record<string, number>;
}

/** Las puertas de lo cargado: objetos cuyo asset es una puerta y que dicen a qué interior llevan. */
function puertasDe(doc: MundoDoc): Puerta[] {
  const out: Puerta[] = [];
  for (const o of doc.objetos) {
    if (doc.assets[o.asset]?.funcion?.tipo !== "puerta" || !o.datos?.interior) continue;
    out.push({ interior: o.datos.interior, x: o.pos[0], y: o.pos[1], z: o.pos[2], yaw: o.rot[1] });
  }
  return out;
}

/** Los usables de un interior con su uso (`{"<n>": uso}`), para el extraplano, donde el servidor no tiene el mundo. */
function usablesDe(b: Bajado): Record<string, string> {
  const out: Record<string, string> = {};
  for (const o of b.interior.objetos) {
    const f = b.assets[o.asset]?.funcion;
    if (f?.tipo === "usable" && o.datos?.n !== undefined) out[String(o.datos.n)] = f.uso;
  }
  return out;
}

export function activarInteriores(mundo: Mundo, sesion: Sesion | null): void {
  const ctx = mundo.contexto;
  let puertas = puertasDe(ctx.doc());
  const bajados = new Map<string, Bajado>();
  const pidiendo = new Map<string, Promise<Bajado | null>>();
  let dentro: Dentro | null = null;
  let ocupado = false;
  let fondoFuera = ctx.escena.background;

  // Lo que también se ve dentro: las luces y tu robot (los demás se ponen solos en `remotos.ts`).
  ctx.escena.traverse((o) => {
    if ((o as { isLight?: boolean }).isLight) o.layers.enable(CAPA_INTERIOR);
  });
  ctx.yo.objeto.traverse((o) => o.layers.enable(CAPA_INTERIOR));
  ctx.yo.cuerda.layers.enable(CAPA_INTERIOR);

  const aviso = document.createElement("button");
  aviso.type = "button";
  aviso.className = "casino-aviso interior-aviso";
  aviso.hidden = true;
  ctx.capa.append(aviso);
  let accion: (() => void) | null = null;
  let nota = 0; // hasta cuándo se enseña un mensaje en vez del aviso
  const mostrar = (texto: string): void => {
    aviso.textContent = texto;
    aviso.hidden = false;
    accion = null;
    nota = performance.now() + 3500;
  };
  const pulsa = (texto: string): string => (ctx.tactil ? `Toca aquí para ${texto}` : `Pulsa E para ${texto}`);
  const ofrecer = (texto: string | null, f: (() => void) | null): void => {
    if (performance.now() < nota) return;
    accion = f;
    aviso.hidden = texto === null;
    if (texto !== null && aviso.textContent !== texto) aviso.textContent = texto;
  };
  const hacer = (): void => {
    if (!ocupado && accion) accion();
  };
  aviso.addEventListener("click", hacer);
  window.addEventListener("keydown", (e) => {
    if (e.code !== "KeyE" || e.repeat || e.ctrlKey || e.metaKey || e.altKey || ctx.camara.anclada) return;
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
    hacer();
  });

  const pedir = async <T>(cuerpo: Record<string, unknown>): Promise<T> => {
    const r = await fetch("/api/interior", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${sesion?.session ?? "dev"}` },
      body: JSON.stringify({ guild_id: sesion?.guildId, channel_id: sesion?.channelId, mundo: PRUEBAS ? "pruebas" : undefined, ...cuerpo }),
    });
    const datos = (await r.json().catch(() => null)) as (T & { error?: string }) | null;
    if (!r.ok || !datos) throw new Error(datos?.error ?? `HTTP ${r.status}`);
    return datos;
  };

  /** Baja (una vez) el interior de una puerta y los assets que usa y no están ya cargados. */
  const bajar = (p: Puerta): Promise<Bajado | null> => {
    const hecho = bajados.get(p.interior);
    if (hecho) return Promise.resolve(hecho);
    let pedido = pidiendo.get(p.interior);
    if (pedido) return pedido;
    const fuente = ctx.fuente();
    if (!fuente) return Promise.resolve(null);
    pedido = (async (): Promise<Bajado | null> => {
      const interior = (await fuente.trozos([`i/${p.interior}`]))[`i/${p.interior}`] as Interior | undefined;
      if (!interior) return null;
      const cargados = ctx.doc().assets;
      const assets: Record<string, Asset> = {};
      const faltan: string[] = [];
      for (const id of new Set(interior.objetos.map((o) => o.asset))) {
        if (cargados[id]) assets[id] = cargados[id];
        else faltan.push(`a/${id}`);
      }
      for (let i = 0; i < faltan.length; i += LOTE) {
        const docs = await fuente.trozos(faltan.slice(i, i + LOTE));
        for (const [k, a] of Object.entries(docs)) assets[k.slice(2)] = a as Asset;
      }
      const b = { interior, assets, x: p.x, z: p.z };
      if (ctx.fuente() === fuente) bajados.set(p.interior, b);
      return b;
    })().finally(() => pidiendo.delete(p.interior));
    pedido.catch((e: unknown) => console.warn("No se ha podido bajar el interior", p.interior, e));
    pidiendo.set(p.interior, pedido);
    return pedido;
  };

  /** Los trozos que mira el servidor para dejarte entrar: la celda de la puerta, el interior y los assets de sus usables. */
  const trozosPuerta = (p: Puerta, b: Bajado): string => {
    const indice = ctx.fuente()?.indice.trozos;
    const claves = [claveCelda(p.x, p.z), `i/${p.interior}`, ...new Set(b.interior.objetos.filter((o) => o.datos?.n !== undefined).map((o) => `a/${o.asset}`))];
    return claves
      .filter((k) => indice?.has(k))
      .slice(0, LOTE)
      .map((k) => `${k}@${indice?.get(k)}`)
      .join(",");
  };

  const entrar = async (p: Puerta): Promise<void> => {
    ocupado = true;
    try {
      const b = await bajar(p);
      if (!b) return mostrar("Esta puerta no lleva a ningún sitio.");
      let r: RespuestaEntrar = { lugar: p.interior, propio: false, piloto: false, puedes: true, estados: {} };
      if (sesion) {
        r = await pedir<RespuestaEntrar>({
          accion: "entrar",
          interior: p.interior,
          t: trozosPuerta(p, b),
          ...(PRUEBAS ? { origen: [p.x, p.y, p.z], piso: b.interior.piso === true, usables: usablesDe(b) } : {}),
        });
      }
      const m = montar(b.interior, b.assets, [p.x, p.y, p.z], r.estados);
      ctx.escena.add(m.grupo);
      fijarCaja(m.caja);
      soloEstos(m.circulos);
      ctx.camara3.layers.set(CAPA_INTERIOR);
      fondoFuera = ctx.escena.background;
      ctx.escena.background = FONDO_DENTRO;
      ctx.yo.teletransportar(m.entrada.x, m.entrada.z);
      ctx.camara.yaw = 0; // mirando hacia -z, al fondo de la habitación
      ctx.camara.dist = Math.min(ctx.camara.dist, 5);
      ctx.calidad.callar(2); // montar la habitación da un tirón que no dice nada de los fps
      dentro = { puerta: p, m, puedes: r.puedes };
      mostrar(r.propio ? `${b.interior.nombre}: tu piso.` : r.piloto ? `${b.interior.nombre}: piso piloto. Con vivienda habitual en «pisos», entrarías en el tuyo.` : b.interior.nombre);
    } catch (e) {
      mostrar(e instanceof Error ? e.message : "No se ha podido entrar.");
    } finally {
      ocupado = false;
    }
  };

  const salir = (): void => {
    const d = dentro;
    if (!d) return;
    dentro = null;
    d.m.soltar();
    fijarCaja(null);
    soloEstos(null);
    ctx.camara3.layers.set(0);
    ctx.escena.background = fondoFuera;
    // Fuera, delante de la puerta y de espaldas a ella.
    const fx = Math.sin(d.puerta.yaw);
    const fz = Math.cos(d.puerta.yaw);
    ctx.yo.teletransportar(d.puerta.x + fx * 1.3, d.puerta.z + fz * 1.3);
    ctx.camara.yaw = Math.atan2(-fx, -fz);
    ctx.calidad.callar(2);
    if (sesion) pedir({ accion: "salir" }).catch(() => undefined);
  };

  const usar = async (u: UsableMontado): Promise<void> => {
    const d = dentro;
    if (!d) return;
    ocupado = true;
    try {
      let estado: number | null;
      if (sesion) estado = (await pedir<{ estado: number }>({ accion: "usar", n: u.n })).estado;
      else estado = u.uso === "mueble" ? 1 - u.estado : u.estado === 0 ? 1 : null;
      if (estado !== null && dentro === d) d.m.poner(u.n, estado);
    } catch (e) {
      mostrar(e instanceof Error ? e.message : "No se ha podido usar.");
    } finally {
      ocupado = false;
    }
  };

  // Alguien de dentro ha usado algo: se ve igual que si lo hubieras usado tú.
  window.addEventListener("jv:uso", (e) => {
    const m = (e as CustomEvent<{ n: number; estado: number }>).detail;
    dentro?.m.poner(m.n, m.estado);
  });
  ctx.alCargar((doc) => (puertas = puertasDe(doc)));

  /** Qué ofrecer dentro: lo más cercano entre la salida y lo que se puede usar. */
  const dentroTick = (d: Dentro): void => {
    const { x, z } = ctx.yo;
    let mejor = Math.hypot(x - d.m.salida.x, z - d.m.salida.z);
    let texto: string | null = mejor <= ALCANCE_SALIDA ? pulsa("salir") : null;
    let f: (() => void) | null = texto ? salir : null;
    if (texto === null) mejor = Infinity;
    for (const u of d.m.usables.values()) {
      const dist = Math.hypot(x - u.x, z - u.z);
      if (dist > ALCANCE_USO || dist >= mejor) continue;
      mejor = dist;
      if (!d.puedes) {
        texto = `${u.nombre} (este piso no es tuyo)`;
        f = null;
      } else if (u.uso === "reparar" && u.estado === 1) {
        texto = `${u.nombre}: arreglado`;
        f = null;
      } else {
        texto = pulsa(`${u.uso === "reparar" ? "arreglar" : u.estado ? "quitar" : "colocar"}: ${u.nombre}`);
        f = () => void usar(u);
      }
    }
    ofrecer(texto, f);
  };

  let cuenta = 0;
  ctx.enganchar(() => {
    if (dentro) return dentroTick(dentro);
    if (cuenta++ % 6) return; // fuera, mirar las puertas diez veces por segundo basta
    const { x, z } = ctx.yo;
    let cerca: Puerta | null = null;
    let dCerca = Infinity;
    for (const p of puertas) {
      const d = Math.hypot(x - p.x, z - p.z);
      if (d <= PRECARGA) void bajar(p);
      if (d < dCerca) [cerca, dCerca] = [p, d];
    }
    for (const [id, b] of bajados) if (Math.hypot(x - b.x, z - b.z) > SUELTA) bajados.delete(id);
    if (cerca && dCerca <= ALCANCE) {
      const nombre = bajados.get(cerca.interior)?.interior.nombre;
      const p = cerca;
      ofrecer(pulsa(nombre ? `entrar: ${nombre}` : "entrar"), () => void entrar(p));
    } else ofrecer(null, null);
  });

  const jv = (window as unknown as { __jv?: Record<string, unknown> }).__jv;
  if (jv) jv.interior = { dentro: () => dentro !== null, salir, entrar: (i: number) => entrar(puertas[i]), puertas: () => puertas };
}
