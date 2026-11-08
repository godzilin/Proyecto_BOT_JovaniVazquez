// Devtools del Jovaniverso, solo para los desarrolladores del bot (el servidor dice quién con `dev` en /api/entrar).
import { BufferAttribute, BufferGeometry, LineBasicMaterial, LineSegments, MeshBasicMaterial } from "three";
import type { Fog, FogExp2 } from "three";
import type { Asset, MundoDoc, Objeto } from "../editor/formato";
import type { Mundo } from "../mundo";
import { BORDE as LIMITE_XZ } from "../mundo/borde";
import { todosLosCirculos } from "../mundo/colision";
import { PRUEBAS, irAMundo } from "../mundo/modo";
import { pistaDoc } from "../mundo/pista";
import { mundoDeSerie } from "../mundo/serie";
import { sueloEn } from "../mundo/terreno";

const MAX_OBJETOS = 200;
const SEG_ANILLO = 24;
const DOBLE_ESPACIO_MS = 300;
const REFRESCO_MS = 250;
const COLISIONES_MS = 2000;
const ENVIO_FANTASMAS_MS = 100;

function el<K extends keyof HTMLElementTagNameMap>(tag: K, clase?: string, texto?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (clase) e.className = clase;
  if (texto !== undefined) e.textContent = texto;
  return e;
}

function boton(texto: string, f: () => void, clase = "dev-b"): HTMLButtonElement {
  const b = el("button", clase, texto);
  b.addEventListener("click", f);
  return b;
}

/**
 * Arranca las devtools: botón 🧪 en el HUD, panel con F2, teclas de vuelo y medidor. `sesion` sirve para pedir los assets
 * publicados; con `gestor` (los desarrolladores del bot) sale también la sección de permisos.
 */
export function iniciarDevtools(mundo: Mundo, sesion: { session: string; guildId: string } | null, gestor = false): void {
  const ctx = mundo.contexto;
  const yo = ctx.yo;
  const escena = ctx.escena;
  const remotos = mundo.remotos;

  // ---- Interfaz ----
  const panel = el("div", "dev-panel");
  panel.hidden = true;
  const abrir = el("button", "dev-abrir", "🧪");
  abrir.title = "Devtools (F2)";
  const alternarPanel = (): void => {
    panel.hidden = !panel.hidden;
  };
  abrir.addEventListener("click", alternarPanel);

  const cabecera = el("div", "dev-cabecera");
  cabecera.append(el("h2", "dev-titulo", "Devtools"), boton("✕", alternarPanel));
  panel.append(cabecera);

  const seccion = (titulo: string): HTMLElement => {
    const s = el("section", "dev-seccion");
    s.append(el("h3", "dev-h", titulo));
    panel.append(s);
    return s;
  };
  const vuelo = el("div", "dev-vuelo");
  vuelo.hidden = true;
  const sincronizadores: (() => void)[] = [];
  const sincronizar = (): void => {
    for (const f of sincronizadores) f();
    vuelo.hidden = !(ctx.tactil && (yo.volar || yo.noclip));
  };
  /** Casilla ligada a un valor: se refleja sola si cambia por otro lado (teclado). */
  const casilla = (padre: HTMLElement, texto: string, leer: () => boolean, escribir: (v: boolean) => void): void => {
    const l = el("label", "dev-casilla");
    const i = el("input");
    i.type = "checkbox";
    i.checked = leer();
    i.addEventListener("change", () => {
      escribir(i.checked);
      i.blur(); // con el foco en la casilla, las teclas de devtools se ignorarían
      sincronizar();
    });
    l.append(i, el("span", undefined, texto));
    padre.append(l);
    sincronizadores.push(() => (i.checked = leer()));
  };

  // ---- Movimiento ----
  const mov = seccion("Movimiento");
  casilla(mov, "Volar", () => yo.volar, (v) => (yo.volar = v));
  const ponerNoclip = (v: boolean): void => {
    yo.noclip = v;
    ctx.camara.atraviesa = v;
  };
  casilla(mov, "Noclip", () => yo.noclip, ponerNoclip);
  const filaVel = el("label", "dev-fila");
  const rango = el("input");
  rango.type = "range";
  rango.min = "1";
  rango.max = "20";
  rango.step = "1";
  rango.value = String(yo.velocidad);
  const numVel = el("span", "dev-valor", `×${yo.velocidad}`);
  rango.addEventListener("input", () => {
    yo.velocidad = Number(rango.value);
    numVel.textContent = `×${yo.velocidad}`;
  });
  rango.addEventListener("change", () => rango.blur());
  filaVel.append(el("span", undefined, "Velocidad"), rango, numVel);
  mov.append(filaVel, el("p", "dev-nota", "Espacio sube, C baja, doble Espacio alterna Volar, N alterna Noclip."));

  // Subir y bajar: teclado y botones táctiles escriben las mismas dos banderas.
  let arriba = false;
  let abajo = false;
  const aplicarSubir = (): void => {
    yo.subir = (arriba ? 1 : 0) - (abajo ? 1 : 0);
  };
  const botonVuelo = (texto: string, poner: (v: boolean) => void): void => {
    const b = el("button", "dev-vuelo-b", texto);
    const soltar = (): void => {
      poner(false);
      aplicarSubir();
    };
    b.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      poner(true);
      aplicarSubir();
    });
    for (const ev of ["pointerup", "pointercancel", "pointerleave"]) b.addEventListener(ev, soltar);
    vuelo.append(b);
  };
  botonVuelo("⬆", (v) => (arriba = v));
  botonVuelo("⬇", (v) => (abajo = v));

  // ---- Teletransporte ----
  const aviso = el("p", "dev-aviso");
  const tp = seccion("Teletransporte");
  const sitios = el("div", "dev-fila-b");
  for (const s of mundo.sitios) sitios.append(boton(s, () => mundo.irA(s)));
  tp.append(sitios);
  const filaXZ = el("div", "dev-fila");
  const inX = el("input", "dev-num");
  const inZ = el("input", "dev-num");
  for (const [i, n] of [[inX, "X"], [inZ, "Z"]] as const) {
    i.type = "number";
    i.step = "any";
    i.placeholder = n;
    i.setAttribute("aria-label", n);
  }
  filaXZ.append(
    inX,
    inZ,
    boton("Ir", () => {
      const x = inX.valueAsNumber;
      const z = inZ.valueAsNumber;
      if (Number.isFinite(x) && Number.isFinite(z) && Math.abs(x) <= LIMITE_XZ && Math.abs(z) <= LIMITE_XZ) {
        yo.teletransportar(x, z);
        aviso.textContent = "";
      } else aviso.textContent = `X y Z tienen que ser números entre -${LIMITE_XZ} y ${LIMITE_XZ}.`;
    }),
  );
  tp.append(filaXZ);

  // ---- Mundo ----
  seccion("Mundo").append(boton(PRUEBAS ? "Volver a la isla" : "Ir al extraplano", () => irAMundo(!PRUEBAS)));

  // ---- Vista: medidor, colisiones, alambre, niebla ----
  const vis = seccion("Vista");
  const medidor = el("div", "dev-medidor");
  let medidorVisible = true;
  casilla(vis, "Medidor", () => medidorVisible, (v) => {
    medidorVisible = v;
    medidor.hidden = !v;
  });

  let anillos: LineSegments | null = null;
  let materialAnillos: LineBasicMaterial | null = null;
  const quitarAnillos = (): void => {
    if (!anillos) return;
    escena.remove(anillos);
    anillos.geometry.dispose();
    anillos = null;
  };
  const rehacerAnillos = (): void => {
    quitarAnillos();
    materialAnillos ??= new LineBasicMaterial({ color: 0xff2bd6, depthTest: false });
    const circulos = todosLosCirculos();
    const pos = new Float32Array(circulos.length * SEG_ANILLO * 6);
    let k = 0;
    for (const c of circulos) {
      const y = sueloEn(c.x, c.z) + 0.05;
      for (let s = 0; s < SEG_ANILLO; s++) {
        const a0 = (s / SEG_ANILLO) * Math.PI * 2;
        const a1 = ((s + 1) / SEG_ANILLO) * Math.PI * 2;
        pos[k++] = c.x + Math.cos(a0) * c.r;
        pos[k++] = y;
        pos[k++] = c.z + Math.sin(a0) * c.r;
        pos[k++] = c.x + Math.cos(a1) * c.r;
        pos[k++] = y;
        pos[k++] = c.z + Math.sin(a1) * c.r;
      }
    }
    const g = new BufferGeometry();
    g.setAttribute("position", new BufferAttribute(pos, 3));
    anillos = new LineSegments(g, materialAnillos);
    anillos.frustumCulled = false;
    anillos.renderOrder = 999;
    escena.add(anillos);
  };
  let verColisiones = false;
  let proximoAnillos = 0;
  casilla(vis, "Colisiones", () => verColisiones, (v) => {
    verColisiones = v;
    if (v) {
      rehacerAnillos();
      proximoAnillos = performance.now() + COLISIONES_MS;
    } else quitarAnillos();
  });
  let materialAlambre: MeshBasicMaterial | null = null;
  casilla(vis, "Alambre", () => escena.overrideMaterial !== null, (v) => {
    materialAlambre ??= new MeshBasicMaterial({ wireframe: true });
    escena.overrideMaterial = v ? materialAlambre : null;
  });
  let nieblaGuardada: Fog | FogExp2 | null = escena.fog;
  casilla(vis, "Niebla", () => escena.fog !== null, (v) => {
    if (v) escena.fog = nieblaGuardada;
    else {
      nieblaGuardada = escena.fog ?? nieblaGuardada;
      escena.fog = null;
    }
  });

  // ---- Carga: robots fantasma ----
  const ca = seccion("Carga");
  const inN = el("input", "dev-num");
  inN.type = "number";
  inN.min = "0";
  inN.max = "50";
  inN.value = "10";
  inN.setAttribute("aria-label", "Número de robots");
  let fantasmas = 0;
  let temporizador: number | null = null;
  const quitarFantasmas = (): void => {
    if (temporizador !== null) clearInterval(temporizador);
    temporizador = null;
    for (let i = 0; i < fantasmas; i++) remotos.sale(200 + i);
    fantasmas = 0;
  };
  const ponerFantasmas = (): void => {
    quitarFantasmas();
    const n = Math.max(0, Math.min(50, Math.floor(inN.valueAsNumber) || 0));
    if (n === 0) return;
    fantasmas = n;
    const cx = yo.x;
    const cz = yo.z;
    const enviar = (): void => {
      const t = performance.now();
      for (let i = 0; i < fantasmas; i++) {
        const r = 3 + 12 * ((i * 0.618) % 1);
        const th = (t / 1000) * (3 / r) + i * 2.4; // ~3 m/s sea cual sea el radio
        const x = cx + Math.cos(th) * r;
        const z = cz + Math.sin(th) * r;
        remotos.entra(200 + i, `fantasma-${i}`, `Fantasma ${i}`); // la sala puede vaciar Remotos al reconectar
        remotos.pose(200 + i, x, sueloEn(x, z), z, Math.atan2(-Math.sin(th), Math.cos(th)), 1, t);
      }
    };
    enviar();
    temporizador = window.setInterval(enviar, ENVIO_FANTASMAS_MS);
  };
  const filaFan = el("div", "dev-fila");
  filaFan.append(inN, boton("Poner robots", ponerFantasmas), boton("Quitar", quitarFantasmas));
  ca.append(filaFan);

  // ---- Assets (solo extraplano) ----
  const as = seccion("Assets");
  if (!PRUEBAS) as.append(el("p", "dev-nota", "Solo en el extraplano."));
  else iniciarAssets(as, mundo, sesion);
  if (gestor && sesion) iniciarPermisos(seccion("Permisos de este servidor"), sesion);
  panel.append(aviso);

  // ---- Teclas (valen con el panel cerrado) ----
  let ultimoEspacio = 0;
  window.addEventListener("keydown", (e) => {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
    if (e.code === "F2") {
      e.preventDefault();
      alternarPanel();
    } else if (e.code === "Space" && !e.repeat) {
      if (e.timeStamp - ultimoEspacio < DOBLE_ESPACIO_MS) {
        yo.volar = !yo.volar;
        ultimoEspacio = 0;
        sincronizar();
      } else ultimoEspacio = e.timeStamp;
      arriba = true;
      aplicarSubir();
    } else if (e.code === "KeyC" && !e.repeat) {
      abajo = true;
      aplicarSubir();
    } else if (e.code === "KeyN" && !e.repeat) {
      ponerNoclip(!yo.noclip);
      sincronizar();
    }
  });
  window.addEventListener("keyup", (e) => {
    if (e.code === "Space") arriba = false;
    else if (e.code === "KeyC") abajo = false;
    else return;
    aplicarSubir();
  });
  window.addEventListener("blur", () => {
    arriba = abajo = false;
    aplicarSubir();
  });

  // ---- Gancho por fotograma: sin reservas y con el DOM a 4 veces por segundo ----
  const info = ctx.renderer.info.render;
  let fps = 60;
  let proximoDom = 0;
  ctx.capa.append(abrir, medidor, vuelo, panel);
  ctx.enganchar((dt) => {
    if (dt > 0) fps += (Math.min(1 / dt, 240) - fps) * 0.1;
    const t = performance.now();
    if (verColisiones && t >= proximoAnillos) {
      rehacerAnillos();
      proximoAnillos = t + COLISIONES_MS;
    }
    if (t < proximoDom) return;
    proximoDom = t + REFRESCO_MS;
    sincronizar();
    if (!medidorVisible) return;
    medidor.textContent =
      `${fps.toFixed(0)} FPS · ${info.calls} llamadas · ${(info.triangles / 1000).toFixed(1)}k tri\n` +
      `x ${yo.x.toFixed(1)}  y ${yo.y.toFixed(1)}  z ${yo.z.toFixed(1)}\n` +
      `ratio ${ctx.calidad.ratio.toFixed(2)} · sombras ${ctx.calidad.sombras ? "sí" : "no"} · remotos ${remotos.cuantos}`;
  });
}

interface ListaPermisos {
  permisos: Record<string, string>;
  usuarios: { id: string; nombre: string; permisos: string[] }[];
  candidatos: { id: string; nombre: string }[];
}

/**
 * Sección de permisos (solo los desarrolladores): quién puede usar el editor y las devtools en este servidor. Cada fila es
 * alguien con una casilla por permiso; buscar añade filas de miembros sin ninguno. El servidor decide y devuelve la lista.
 */
function iniciarPermisos(padre: HTMLElement, sesion: { session: string; guildId: string }): void {
  const lista = el("div", "dev-permisos");
  const estado = el("p", "dev-nota");
  const buscar = el("input", "dev-texto");
  buscar.type = "search";
  buscar.placeholder = "Buscar miembro por nombre";
  let datos: ListaPermisos = { permisos: {}, usuarios: [], candidatos: [] };

  const llamar = async (body?: Record<string, unknown>, q = ""): Promise<void> => {
    const url = `/api/permisos?guild_id=${encodeURIComponent(sesion.guildId)}${q ? `&q=${encodeURIComponent(q)}` : ""}`;
    const r = await fetch(url, {
      method: body ? "POST" : "GET",
      headers: { Authorization: `Bearer ${sesion.session}`, ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body ? JSON.stringify({ guild_id: sesion.guildId, ...body }) : undefined,
    });
    const j = (await r.json().catch(() => null)) as (ListaPermisos & { error?: string }) | null;
    if (!r.ok || !j) throw new Error(j?.error ?? `HTTP ${r.status}`);
    // Los candidatos de la última búsqueda se quedan tras dar un permiso, para seguir dando.
    datos = { ...j, candidatos: body ? datos.candidatos : j.candidatos };
  };
  const pintar = (): void => {
    const con = new Set(datos.usuarios.map((u) => u.id));
    const filas = [...datos.usuarios, ...datos.candidatos.filter((c) => !con.has(c.id)).map((c) => ({ ...c, permisos: [] as string[] }))];
    lista.replaceChildren(
      ...filas.map((u) => {
        const fila = el("div", "dev-permiso");
        fila.append(el("span", "dev-permiso-nombre", u.nombre));
        for (const [clave, nombre] of Object.entries(datos.permisos)) {
          const l = el("label", "dev-casilla");
          const i = el("input");
          i.type = "checkbox";
          i.checked = u.permisos.includes(clave);
          i.addEventListener("change", () => {
            i.disabled = true;
            hacer(llamar({ user_id: u.id, permiso: clave, dar: i.checked }));
          });
          l.append(i, el("span", undefined, nombre));
          fila.append(l);
        }
        return fila;
      }),
    );
    if (!filas.length) lista.append(el("p", "dev-nota", "Nadie más tiene permisos. Busca a alguien para dárselos."));
  };
  const hacer = (p: Promise<void>): void => {
    estado.textContent = "";
    p.then(pintar, (e: unknown) => {
      estado.textContent = e instanceof Error ? e.message : "No se han podido cambiar los permisos.";
      pintar();
    });
  };
  const fila = el("div", "dev-fila");
  fila.append(buscar, boton("Buscar", () => hacer(llamar(undefined, buscar.value.trim()))));
  buscar.addEventListener("keydown", (e) => {
    if (e.key === "Enter") hacer(llamar(undefined, buscar.value.trim()));
  });
  padre.append(el("p", "dev-nota", "Los desarrolladores los tienen todos. Ser administrador de Discord no da ninguno."), fila, lista, estado);
  hacer(llamar());
}

/** Sección de assets del extraplano: lista filtrable y objetos puestos delante del robot (hasta 200). */
function iniciarAssets(padre: HTMLElement, mundo: Mundo, sesion: { session: string; guildId: string } | null): void {
  const assets: Record<string, Asset> = { ...mundoDeSerie().assets };
  const puestos: Objeto[] = [];
  const yo = mundo.contexto.yo;

  const buscar = el("input", "dev-texto");
  buscar.type = "search";
  buscar.placeholder = "Buscar por nombre";
  const cuenta = el("p", "dev-nota");
  const lista = el("div", "dev-lista");
  const estado = el("p", "dev-nota");
  const fila = el("div", "dev-fila-b");

  const aplicar = (): void => {
    const doc: MundoDoc = pistaDoc();
    doc.assets = { ...doc.assets };
    // Con prefijo: un asset publicado que se llame como los de la pista (`pista`, `columna`) no la pisa.
    for (const o of puestos) doc.assets[`+${o.asset}`] = assets[o.asset];
    doc.objetos = [...doc.objetos, ...puestos.map((o) => ({ ...o, asset: `+${o.asset}` }))];
    mundo.cargarMundo(doc);
    estado.textContent = puestos.length >= MAX_OBJETOS ? `Límite de ${MAX_OBJETOS} objetos puestos.` : `Objetos puestos: ${puestos.length}.`;
  };
  const poner = (id: string): void => {
    if (puestos.length >= MAX_OBJETOS) {
      estado.textContent = `Límite de ${MAX_OBJETOS} objetos puestos.`;
      return;
    }
    const cm = (v: number): number => Math.round(v * 100) / 100;
    // A 3 m del robot más lo que mide el asset (sus círculos de colisión), para no ponérselo encima.
    const d = 3 + Math.max(0, ...(assets[id].colision ?? []).map(([cx, cz, r]) => Math.hypot(cx, cz) + r));
    const x = yo.x + Math.sin(yo.giro) * d;
    const z = yo.z + Math.cos(yo.giro) * d;
    puestos.push({ asset: id, pos: [cm(x), cm(sueloEn(x, z)), cm(z)], rot: [0, cm(yo.giro), 0], esc: [1, 1, 1] });
    aplicar();
  };
  const pintar = (): void => {
    const q = buscar.value.trim().toLowerCase();
    const ids = Object.keys(assets).filter((id) => assets[id].nombre.toLowerCase().includes(q));
    cuenta.textContent = `${ids.length} de ${Object.keys(assets).length} assets`;
    lista.replaceChildren(...ids.map((id) => boton(assets[id].nombre, () => poner(id), "dev-b dev-item")));
  };
  buscar.addEventListener("input", pintar);
  fila.append(
    boton("Deshacer", () => {
      if (puestos.pop()) aplicar();
    }),
    boton("Quitar todo", () => {
      puestos.length = 0;
      aplicar();
    }),
  );
  padre.append(buscar, cuenta, lista, fila, estado);
  pintar();

  if (sesion) {
    fetch(`/api/mundo?guild_id=${encodeURIComponent(sesion.guildId)}`, { headers: { Authorization: `Bearer ${sesion.session}` } })
      .then((r) => (r.ok ? (r.json() as Promise<{ version: number; trozos: Record<string, Asset> }>) : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((r) => {
        for (const [clave, a] of Object.entries(r.trozos)) if (clave.startsWith("a/")) assets[clave.slice(2)] = a; // el publicado gana en ids repetidos
        pintar();
      })
      .catch((e: unknown) => console.warn("Devtools: sin los assets publicados.", e));
  }
}
