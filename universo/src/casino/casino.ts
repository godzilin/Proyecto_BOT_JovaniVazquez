// Las tragaperras de la plaza: pantallas, luces, botones físicos, sentarse en primera persona, tiradas contra el servidor,
// ▶️ Auto, sonido y el HUD. Se carga con import() al acercarse a la plaza (ver entrada.ts).
import {
  CanvasTexture,
  Color,
  CylinderGeometry,
  Float32BufferAttribute,
  IcosahedronGeometry,
  InstancedMesh,
  LinearFilter,
  Matrix4,
  Mesh,
  MeshBasicMaterial,
  PlaneGeometry,
  Raycaster,
  SRGBColorSpace,
  Vector2,
  Vector3,
  BoxGeometry,
} from "three";
import type { BufferGeometry, Object3D } from "three";
import { fundir } from "../mundo/geom";
import { PLAZA } from "../mundo/terreno";
import type { Contexto } from "../mundo";
import type { Sesion } from "../paseo";
import {
  ATLAS_H,
  ATLAS_W,
  ETIQUETAS,
  LETRERO,
  PANTALLA_H,
  PANTALLA_W,
  Pintor,
  dibujarEtiqueta,
  dibujarLetrero,
  dibujarNumero,
  dibujarVisor,
  etiquetaDe,
  numeroDe,
  visorDe,
} from "./dibujo";
import type { DatosVisor, Rect, Tragas } from "./dibujo";
import { Reproduccion, girando } from "./repro";
import type { Estado, Suceso } from "./repro";
import { ALTO_LETRERO, ARCO, CUBIERTA, MAQUINAS, PANTALLA, PUESTOS, aMundo, asiento, frenteDe, matrizDe } from "./sitio";
import { Sonido } from "./sonido";
import type { Manejo, Zumbido } from "./sonido";

export interface ConfigCasino {
  tragas: Tragas;
  /** `null` en las pruebas locales (`?debug` fuera de Discord): el servidor de pruebas no pide sesión. */
  sesion: Sesion | null;
  /** Para poner el saldo en la píldora del HUD. */
  saldo(texto: string): void;
}

interface Respuesta {
  machine: number;
  states: Estado[];
  bet: number;
  won: number;
  jackpot: number;
  tier: string | null;
  near_miss: boolean;
  free_won: boolean;
  balance_text: string;
  pot_text: string;
  heat: number;
  heat_max: number;
  bonus_fill: number;
  free_spins: number;
  text: string;
  auto: boolean;
  auto_stop: string | null;
}

// -- Medidas y colores ---------------------------------------------------------------------------------------------

/** Botones: 0 GIRAR, 1 ½, 2 ×2, 3 AUTO, 4 PARAR (el orden de ETIQUETAS). x y z en el marco de la botonera. */
const BOTONES = [
  { x: 0, r: 0.11, color: 0x2fbf4a },
  { x: -0.31, r: 0.075, color: 0x3f7fd8 },
  { x: 0.31, r: 0.075, color: 0xd89a3f },
  { x: -0.6, r: 0.075, color: 0xe8c020 },
  { x: 0.6, r: 0.075, color: 0xd03030 },
];
const Z_BOTON = 0.08;
const Z_ETIQUETA = 0.265;
const SUPERFICIE = 0.052; // altura de la cara de arriba de la botonera, en su marco
const BOMBILLAS = 29; // por máquina: 12 + 12 a los lados de la pantalla, 4 del rótulo y la luz de AUTO
const LUZ_AUTO = 28;
const ALCANCE_BOTON = 3.8; // m desde la cámara
const ALCANCE_TOQUE = 6;
const DISTANCIA_PANTALLAS = 45; // más lejos no se pintan
const DISTANCIA_LUCES = 90; // más lejos no se dibujan ni las luces, ni los botones, ni los letreros
const MAX_PINTADAS = 4;
const APUESTA_INICIAL = 100;
const APUESTA_MAX = 10_000_000;
const LEJOS_AUTO = 45; // m al centro de la plaza: más lejos el Auto se para
const COLORES_CARTEL: Record<string, number> = { big: 0xffd24a, mega: 0xff7a2a, epic: 0xff3aa0 };
const TEXTO_AVISO = "Toca la máquina o pulsa E para jugar";
const TEXTO_AVISO_TACTIL = "Toca la máquina para jugar";

const rad = (g: number): number => (g * Math.PI) / 180;
const limitar = (v: number, a: number, b: number): number => Math.min(b, Math.max(a, v));

interface Maquina {
  i: number;
  g: CanvasRenderingContext2D;
  tex: CanvasTexture;
  malla: Mesh;
  caja: Mesh;
  apuesta: number;
  auto: boolean;
  ocupada: boolean;
  repro: Reproduccion | null;
  resp: Respuesta | null;
  quieto: Estado;
  pintado: number;
  sucio: boolean;
  vista: boolean;
  ultimoPintado: number;
  siguiente: number;
  visor: DatosVisor;
  zumbido: Zumbido | null;
  tension: Manejo | null;
  pulso: number[];
  pos: Vector3;
}

/** Quita lo de Discord (`### `, `-# `, `**`) para enseñar el resultado como texto normal. */
export function limpiarTexto(texto: string): { linea: string; menor: boolean }[] {
  return texto
    .split("\n")
    .map((l) => ({ menor: l.startsWith("-#"), linea: l.replace(/^(#{1,3}|-#)\s*/, "").replace(/(\*\*|__|`)/g, "").trim() }))
    .filter((l) => l.linea);
}

// -- El módulo ---------------------------------------------------------------------------------------------------------

export async function montarCasino(ctx: Contexto, cfg: ConfigCasino): Promise<void> {
  const { tragas, sesion } = cfg;
  const sonido = new Sonido(ctx.tactil);
  void sonido.cargar();
  const pintor = await Pintor.crear(tragas);
  const largo = tragas.strips.map((s) => s.length);

  // Atlas: letrero, visores, etiquetas y números en una sola textura (una llamada de dibujo).
  const lienzoAtlas = document.createElement("canvas");
  lienzoAtlas.width = ATLAS_W;
  lienzoAtlas.height = ATLAS_H;
  const ga = lienzoAtlas.getContext("2d")!;
  const atlas = new CanvasTexture(lienzoAtlas);
  atlas.colorSpace = SRGBColorSpace;
  atlas.minFilter = LinearFilter;
  atlas.generateMipmaps = false;
  atlas.anisotropy = 4;
  let bote = tragas.pot_text;
  ETIQUETAS.forEach((_, k) => dibujarEtiqueta(ga, k));
  for (let i = 0; i < MAQUINAS; i++) dibujarNumero(ga, i);
  dibujarLetrero(ga, bote);

  const maquinas: Maquina[] = [];
  const objetos: Object3D[] = [];
  const aviso = document.createElement("div");

  // Planos del atlas: letrero, 6 visores, 30 etiquetas y 6 números, todos en una malla.
  const plano = (w: number, h: number, m: Matrix4, r: Rect): BufferGeometry => {
    const g = new PlaneGeometry(w, h);
    const uv = g.getAttribute("uv");
    for (let k = 0; k < uv.count; k++) uv.setXY(k, (r.x + uv.getX(k) * r.w) / ATLAS_W, 1 - (r.y + (1 - uv.getY(k)) * r.h) / ATLAS_H);
    return g.applyMatrix4(m);
  };
  const tumbado = new Matrix4().makeRotationX(-Math.PI / 2); // plano acostado en la botonera, con el texto leyéndose desde delante
  const botonera = (i: number): Matrix4 =>
    matrizDe(PUESTOS[i], new Matrix4()).multiply(new Matrix4().makeTranslation(0, CUBIERTA.y, CUBIERTA.z)).multiply(new Matrix4().makeRotationX(CUBIERTA.inclinacion));
  const enBotonera = (i: number, x: number, z: number): Matrix4 => botonera(i).multiply(new Matrix4().makeTranslation(x, SUPERFICIE, z)).multiply(tumbado);
  const enMaquina = (i: number, x: number, y: number, z: number): Matrix4 => matrizDe(PUESTOS[i], new Matrix4()).multiply(new Matrix4().makeTranslation(x, y, z));

  const planos: BufferGeometry[] = [plano(7, 0.875, matrizDe(ARCO, new Matrix4()).multiply(new Matrix4().makeTranslation(0, ALTO_LETRERO, 0.09)), LETRERO)];
  for (let i = 0; i < MAQUINAS; i++) {
    planos.push(plano(0.71, 0.2, enBotonera(i, 0, -0.16), visorDe(i)));
    planos.push(plano(0.16, 0.16, enMaquina(i, -0.66, 2.425, 0.352), numeroDe(i)));
    planos.push(plano(0.3, 0.096, enMaquina(i, 0.4, 2.425, 0.352), etiquetaDe(3)));
    BOTONES.forEach((b, k) => planos.push(plano(k === 0 ? 0.28 : 0.2, k === 0 ? 0.0896 : 0.064, enBotonera(i, b.x, Z_ETIQUETA), etiquetaDe(k))));
  }
  for (const g of planos) g.deleteAttribute("normal");
  const carteles = new Mesh(fundir(planos), new MeshBasicMaterial({ map: atlas, toneMapped: false }));
  objetos.push(carteles);

  // Botones: un InstancedMesh con las 30 tapas. Color por vértice (arriba claro, canto oscuro) por instancia.
  const tapa = new CylinderGeometry(0.075, 0.085, 0.05, 16);
  {
    const n = tapa.getAttribute("normal");
    const c: number[] = [];
    for (let k = 0; k < n.count; k++) c.push(...(n.getY(k) > 0.5 ? [1, 1, 1] : n.getY(k) < -0.5 ? [0.4, 0.4, 0.4] : [0.72, 0.72, 0.72]));
    tapa.setAttribute("color", new Float32BufferAttribute(c, 3));
  }
  const botones = new InstancedMesh(tapa, new MeshBasicMaterial({ vertexColors: true, toneMapped: false }), MAQUINAS * BOTONES.length);
  objetos.push(botones);

  // Bombillas: icosaedros pequeños; el color de cada una cambia solo cuando cambia el estado de su máquina.
  const bombillas = new InstancedMesh(new IcosahedronGeometry(0.03, 0), new MeshBasicMaterial({ toneMapped: false }), MAQUINAS * BOMBILLAS);
  objetos.push(bombillas);
  const m4 = new Matrix4();
  for (let i = 0; i < MAQUINAS; i++) {
    for (let k = 0; k < BOMBILLAS; k++) {
      let x = 0;
      let y = 2.425;
      let z = 0.37;
      let e = 1;
      if (k < 24) {
        x = k < 12 ? -0.6 : 0.6;
        y = PANTALLA.y - 0.45 + (0.9 * ((k % 12) + 0.5)) / 12;
        z = 0.362;
      } else if (k < 28) {
        x = -0.42 + (k - 24) * 0.18;
      } else {
        x = 0.7;
        e = 2.2;
      }
      m4.copy(enMaquina(i, x, y, z)).scale(new Vector3(e, e, e));
      bombillas.setMatrixAt(i * BOMBILLAS + k, m4);
      bombillas.setColorAt(i * BOMBILLAS + k, new Color(0x20202a));
    }
  }
  bombillas.computeBoundingSphere();

  // Pantallas y cajas de toque.
  const caja = new BoxGeometry(1.8, 2.8, 1.9);
  for (let i = 0; i < MAQUINAS; i++) {
    const lienzo = document.createElement("canvas");
    lienzo.width = PANTALLA_W;
    lienzo.height = PANTALLA_H;
    const g = lienzo.getContext("2d")!;
    const tex = new CanvasTexture(lienzo);
    tex.colorSpace = SRGBColorSpace;
    tex.minFilter = LinearFilter;
    tex.generateMipmaps = false;
    const malla = new Mesh(new PlaneGeometry(PANTALLA.w, PANTALLA.h), new MeshBasicMaterial({ map: tex, toneMapped: false }));
    const p = PUESTOS[i];
    malla.position.copy(aMundo(i, 0, PANTALLA.y, PANTALLA.z + 0.012));
    malla.rotation.y = p.yaw;
    malla.updateMatrixWorld();
    ctx.escena.add(malla);
    ctx.culling.registrar(malla, { radio: 0.8, distancia: DISTANCIA_PANTALLAS, ocultar: true });
    // No va en la escena: solo sirve para el rayo del toque.
    const cajaToque = new Mesh(caja);
    cajaToque.position.copy(aMundo(i, 0, 1.4, 0.35));
    cajaToque.rotation.y = p.yaw;
    cajaToque.updateMatrixWorld();
    const visor: DatosVisor = { apuesta: "", gratis: 0, bonus: 0, calor: 0, calorMax: 5 };
    maquinas.push({
      i,
      g,
      tex,
      malla,
      caja: cajaToque,
      apuesta: APUESTA_INICIAL,
      auto: false,
      ocupada: false,
      repro: null,
      resp: null,
      quieto: tragas.stills[i] as Estado,
      pintado: -2,
      sucio: true,
      vista: false,
      ultimoPintado: 0,
      siguiente: 0,
      visor,
      zumbido: null,
      tension: null,
      pulso: BOTONES.map(() => 0),
      pos: aMundo(i, 0, 1.4, 0.5),
    });
  }
  for (const o of objetos) {
    ctx.escena.add(o);
    ctx.culling.registrarMalla(o as Mesh, DISTANCIA_LUCES);
  }

  // -- Botones y luces ---------------------------------------------------------------------------------------------

  const color = new Color();
  const cBase = BOTONES.map((b) => new Color(b.color));
  let botonesSucios = true;
  let repintarEn = 0; // cuándo vuelve a subir un botón recién pulsado
  const matrizBoton = (i: number, k: number, hundido: number): Matrix4 => {
    const b = BOTONES[k];
    const e = b.r / 0.075;
    return botonera(i)
      .multiply(new Matrix4().makeTranslation(b.x, SUPERFICIE + 0.025 - hundido, Z_BOTON))
      .scale(new Vector3(e, 1, e));
  };
  const pintarBotones = (ahora: number): void => {
    for (const m of maquinas) {
      for (let k = 0; k < BOTONES.length; k++) {
        const hundido = m.pulso[k] > ahora;
        const encendido = (k === 0 && m.ocupada) || ((k === 3 || k === 4) && m.auto) || hundido;
        botones.setMatrixAt(m.i * BOTONES.length + k, matrizBoton(m.i, k, hundido ? 0.016 : 0));
        botones.setColorAt(m.i * BOTONES.length + k, color.copy(cBase[k]).multiplyScalar(hundido ? 2.1 : encendido ? 1.7 : 0.7));
      }
    }
    botones.instanceMatrix.needsUpdate = true;
    if (botones.instanceColor) botones.instanceColor.needsUpdate = true;
    botones.computeBoundingSphere();
    botonesSucios = false;
  };

  const luces = (m: Maquina, e: Estado, ahora: number): void => {
    const base = m.i * BOMBILLAS;
    const lit = e.bulbs.lit;
    for (let k = 0; k < 24; k++) {
      const c = lit[k % 12];
      bombillas.setColorAt(base + k, c ? color.set(c).multiplyScalar(1.5) : color.set(0x1d1d26));
    }
    const parpadeo = Math.floor(ahora / 140) % 2 === 0;
    const cartel = e.banner ? COLORES_CARTEL[e.banner.tier] : e.glow > 0 ? new Color(String(e.glowColor ?? "#60c4ff")).getHex() : 0;
    for (let k = 0; k < 4; k++) {
      const encendida = cartel !== 0 && (k % 2 === (parpadeo ? 0 : 1) || !e.banner);
      bombillas.setColorAt(base + 24 + k, encendida ? color.set(cartel).multiplyScalar(1.6) : color.set(0x3a1d1d));
    }
    bombillas.setColorAt(base + LUZ_AUTO, m.auto ? color.set(0x46ff78).multiplyScalar(1.6) : color.set(0x16261b));
    if (bombillas.instanceColor) bombillas.instanceColor.needsUpdate = true;
  };

  const escribirVisor = (m: Maquina): void => {
    m.visor.apuesta = `${m.apuesta.toLocaleString("es-ES")} Y$`;
    dibujarVisor(ga, m.i, m.visor);
    atlas.needsUpdate = true;
  };
  maquinas.forEach(escribirVisor);

  // -- HUD ---------------------------------------------------------------------------------------------------------

  const el = <T extends HTMLElement>(tag: string, clase: string, padre: HTMLElement = ctx.capa): T => {
    const e = document.createElement(tag) as T;
    e.className = clase;
    padre.append(e);
    return e;
  };
  aviso.className = "casino-aviso";
  aviso.hidden = true;
  aviso.setAttribute("role", "status");
  ctx.capa.append(aviso);
  const barra = el<HTMLDivElement>("div", "casino-barra");
  const botonSonido = el<HTMLButtonElement>("button", "casino-boton", barra);
  botonSonido.type = "button";
  const textoSonido = (): void => {
    botonSonido.textContent = sonido.silenciado ? "🔇" : "🔊";
    botonSonido.setAttribute("aria-label", sonido.silenciado ? "Activar el sonido de las tragaperras" : "Silenciar las tragaperras");
  };
  textoSonido();
  botonSonido.addEventListener("click", () => {
    sonido.silenciado = !sonido.silenciado;
    sonido.activar();
    textoSonido();
    botonSonido.blur();
  });
  const botonLevantar = el<HTMLButtonElement>("button", "casino-boton", barra);
  botonLevantar.type = "button";
  botonLevantar.hidden = true;
  botonLevantar.textContent = "✕";
  botonLevantar.setAttribute("aria-label", "Levantarse de la máquina");
  const panel = el<HTMLDivElement>("div", "casino-resultado");
  panel.hidden = true;
  panel.setAttribute("role", "status");
  let ocultarPanel = 0;
  const mostrar = (cabecera: string, texto: string, error = false): void => {
    panel.replaceChildren();
    panel.classList.toggle("error", error);
    const c = document.createElement("div");
    c.className = "casino-maquina";
    c.textContent = cabecera;
    panel.append(c);
    limpiarTexto(texto).forEach(({ linea, menor }, k) => {
      const d = document.createElement("div");
      d.className = menor ? "casino-menor" : k === 0 ? "casino-titulo" : "casino-linea";
      d.textContent = linea;
      panel.append(d);
    });
    panel.hidden = false;
    clearTimeout(ocultarPanel);
    ocultarPanel = window.setTimeout(() => (panel.hidden = true), error ? 5000 : 6500);
  };

  // -- Tiradas -----------------------------------------------------------------------------------------------------

  const pos = (m: Maquina): Vector3 => m.pos;
  const tocar = (m: Maquina, nombre: string, opciones?: { volumen?: number; prioridad?: boolean }): Manejo | null => sonido.poner(nombre, m.i + 1, pos(m), opciones);

  const pedir = async (cuerpo: Record<string, unknown>): Promise<Respuesta> => {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (sesion) headers.Authorization = `Bearer ${sesion.session}`;
    else headers.Authorization = "Bearer dev";
    const r = await fetch("/api/traga", {
      method: "POST",
      headers,
      body: JSON.stringify({ guild_id: sesion?.guildId ?? "1", channel_id: sesion?.channelId ?? "1", ...cuerpo }),
    });
    const datos = (await r.json().catch(() => null)) as (Respuesta & { error?: string }) | null;
    if (!r.ok) throw new Error(datos?.error ?? `HTTP ${r.status}`);
    return datos as Respuesta;
  };

  const parar = (m: Maquina, motivo?: string): void => {
    if (!m.auto) return;
    m.auto = false;
    botonesSucios = true;
    m.sucio = true;
    tocar(m, "auto");
    pedir({ machine: m.i + 1, parar: true }).catch(() => undefined);
    if (motivo) mostrar(`Máquina ${m.i + 1}`, motivo);
  };

  const girar = async (m: Maquina, desdeAuto: boolean): Promise<void> => {
    if (m.ocupada) return;
    m.ocupada = true;
    botonesSucios = true;
    sonido.activar();
    tocar(m, "girar");
    try {
      const r = await pedir({ machine: m.i + 1, stake: m.apuesta, auto: desdeAuto });
      m.resp = r;
      ultima = r;
      m.repro = new Reproduccion(r.states, largo, performance.now());
      m.zumbido = sonido.zumbido(m.i + 1, pos(m));
    } catch (error) {
      m.ocupada = false;
      botonesSucios = true;
      const texto = error instanceof Error ? error.message : "No se ha podido jugar.";
      if (desdeAuto && /aún está girando/.test(texto)) {
        m.siguiente = performance.now() + 500;
        return;
      }
      if (m.auto) parar(m);
      mostrar(`Máquina ${m.i + 1}`, texto, true);
    }
  };

  const terminar = (m: Maquina, ahora: number): void => {
    const r = m.resp!;
    m.quieto = m.repro!.estados[m.repro!.estados.length - 1];
    m.repro = null;
    m.resp = null;
    m.ocupada = false;
    m.sucio = true;
    m.zumbido?.parar();
    m.zumbido = null;
    m.tension?.parar();
    m.tension = null;
    m.apuesta = r.bet;
    m.visor.gratis = r.free_spins;
    m.visor.bonus = r.bonus_fill;
    m.visor.calor = r.heat;
    m.visor.calorMax = r.heat_max;
    escribirVisor(m);
    if (r.pot_text !== bote) {
      bote = r.pot_text;
      dibujarLetrero(ga, bote);
      atlas.needsUpdate = true;
    }
    cfg.saldo(r.balance_text);
    mostrar(`Máquina ${m.i + 1}`, r.text);
    if (r.won > 0 && "vibrate" in navigator) navigator.vibrate(r.won >= m.apuesta * 5 ? [60, 40, 90] : 40);
    if (!r.auto && m.auto) {
      m.auto = false;
      tocar(m, "auto");
      if (r.auto_stop) mostrar(`Máquina ${m.i + 1}: Auto parado`, r.auto_stop);
    } else if (r.auto_stop) {
      mostrar(`Máquina ${m.i + 1}: Auto parado`, r.auto_stop);
    }
    m.siguiente = ahora + 700;
    botonesSucios = true;
  };

  const sonar = (m: Maquina, s: Suceso): void => {
    switch (s.tipo) {
      case "parada":
        tocar(m, s.lenta ? "parada_lenta" : "parada");
        break;
      case "tension":
        if (s.on) m.tension = tocar(m, "tension");
        else {
          m.tension?.parar();
          m.tension = null;
        }
        break;
      case "moneda":
        tocar(m, `moneda_${s.variante}`, { volumen: 0.7 });
        break;
      case "cartel":
        tocar(m, s.tier === "big" ? "granpremio" : s.tier === "mega" ? "mega" : "epico", { prioridad: true });
        break;
      case "resultado": {
        const r = m.resp;
        if (!r) break;
        if (r.jackpot > 0) tocar(m, "bote", { prioridad: true });
        if (r.free_won) tocar(m, "gratis");
        if (r.near_miss && r.won === 0) tocar(m, "casi");
        else if (r.won > 0 && !r.tier && r.jackpot === 0) tocar(m, "premio");
        break;
      }
    }
  };

  // -- Sentarse y pulsar -------------------------------------------------------------------------------------------

  let sentada: number | null = null;
  const sentar = (i: number): void => {
    sonido.activar();
    const c3 = ctx.camara3;
    const mitadAncha = Math.tan(rad(c3.fov / 2)) * c3.aspect;
    const a = asiento(i, limitar(0.85 + 0.72 / mitadAncha, 2.2, 2.8));
    ctx.yo.teletransportar(a.pos.x, a.pos.z);
    ctx.camara.anclar(a.pos, a.yaw, c3.aspect < 1 ? 0 : 0.1, { yaw: 1.15, pitchMin: -0.75, pitchMax: 0.85 });
    sentada = i;
    botonLevantar.hidden = false;
  };
  const levantar = (): void => {
    if (sentada === null) return;
    ctx.camara.soltar();
    sentada = null;
    botonLevantar.hidden = true;
  };
  botonLevantar.addEventListener("click", levantar);
  window.addEventListener("resize", () => sentada !== null && sentar(sentada));

  const cercana = (): number | null => {
    let mejor: number | null = null;
    let d2 = 4;
    for (let i = 0; i < MAQUINAS; i++) {
      const f = frenteDe(i);
      const dx = ctx.yo.x - (PUESTOS[i].x + f.x * 1.2);
      const dz = ctx.yo.z - (PUESTOS[i].z + f.z * 1.2);
      if (dx * dx + dz * dz < d2) [mejor, d2] = [i, dx * dx + dz * dz];
    }
    return mejor;
  };

  const pulsar = (m: Maquina, k: number): void => {
    const ahora = performance.now();
    sonido.activar();
    m.pulso[k] = ahora + 170;
    repintarEn = ahora + 180;
    botonesSucios = true;
    tocar(m, "boton");
    if (k === 0) void girar(m, false);
    else if (k === 1) m.apuesta = Math.max(1, Math.floor(m.apuesta / 2));
    else if (k === 2) m.apuesta = Math.min(APUESTA_MAX, m.apuesta * 2);
    else if (k === 3) {
      if (!m.auto) {
        m.auto = true;
        m.sucio = true;
        tocar(m, "auto");
        void girar(m, true);
      }
    } else parar(m);
    if (k === 1 || k === 2) escribirVisor(m);
  };

  const rayo = new Raycaster();
  const ndc = new Vector2();
  type Blanco = { tipo: "boton"; m: Maquina; k: number } | { tipo: "maquina"; m: Maquina };
  const apuntar = (e: PointerEvent): Blanco | null => {
    const r = ctx.lienzo.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -(((e.clientY - r.top) / r.height) * 2 - 1));
    rayo.setFromCamera(ndc, ctx.camara3);
    const ojo = ctx.camara3.position;
    const dePie = (m: Maquina): boolean => {
      const f = frenteDe(m.i);
      return (ojo.x - PUESTOS[m.i].x) * f.x + (ojo.z - PUESTOS[m.i].z) * f.z > 0.2; // delante de la máquina, no detrás
    };
    for (const h of rayo.intersectObject(botones)) {
      if (h.distance > ALCANCE_BOTON || h.instanceId === undefined) break;
      const m = maquinas[Math.floor(h.instanceId / BOTONES.length)];
      if (dePie(m)) return { tipo: "boton", m, k: h.instanceId % BOTONES.length };
    }
    const toques = rayo.intersectObjects(maquinas.map((m) => m.caja));
    if (toques.length && toques[0].distance <= ALCANCE_TOQUE) {
      const m = maquinas.find((x) => x.caja === toques[0].object)!;
      if (dePie(m)) return { tipo: "maquina", m };
    }
    return null;
  };

  const abajo = new Map<number, { x: number; y: number; blanco: Blanco }>();
  ctx.lienzo.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const blanco = apuntar(e);
    if (!blanco) return;
    abajo.set(e.pointerId, { x: e.clientX, y: e.clientY, blanco });
    if (blanco.tipo === "boton") {
      blanco.m.pulso[blanco.k] = performance.now() + 5000; // hundido mientras se mantiene
      botonesSucios = true;
    }
  });
  ctx.lienzo.addEventListener("pointermove", (e) => {
    const d = abajo.get(e.pointerId);
    if (d && Math.hypot(e.clientX - d.x, e.clientY - d.y) > 10) soltarToque(e.pointerId, false);
  });
  const soltarToque = (id: number, ejecutar: boolean): void => {
    const d = abajo.get(id);
    if (!d) return;
    abajo.delete(id);
    const b = d.blanco;
    if (b.tipo === "boton") b.m.pulso[b.k] = 0;
    botonesSucios = true;
    if (!ejecutar) return;
    if (b.tipo === "boton") pulsar(b.m, b.k);
    else if (sentada === null) sentar(b.m.i);
  };
  ctx.lienzo.addEventListener("pointerup", (e) => soltarToque(e.pointerId, true));
  ctx.lienzo.addEventListener("pointercancel", (e) => soltarToque(e.pointerId, false));

  window.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.code === "KeyE" && sentada === null && !e.repeat) {
      const i = cercana();
      if (i !== null) sentar(i);
    } else if (sentada !== null) {
      if (e.code === "Escape") levantar();
      else if (e.code === "Space" && !e.repeat) pulsar(maquinas[sentada], 0);
      else if (e.code === "KeyA" && !e.repeat) pulsar(maquinas[sentada], 3);
    }
  });

  // -- Cada fotograma ----------------------------------------------------------------------------------------------

  let ultimaCercania = -1;
  let ultimaLejania = 0;
  let ultima: Respuesta | null = null;
  const tick = (): void => {
    const ahora = performance.now();
    sonido.escuchar(ctx.camara3);

    const cerca = sentada === null ? cercana() : null;
    if (cerca !== ultimaCercania) {
      ultimaCercania = cerca ?? -1;
      aviso.hidden = cerca === null;
      aviso.textContent = ctx.tactil ? TEXTO_AVISO_TACTIL : TEXTO_AVISO;
    }

    const lejos = Math.hypot(ctx.yo.x - PLAZA.x, ctx.yo.z - PLAZA.z) > LEJOS_AUTO;
    if (ahora - ultimaLejania > 500) {
      ultimaLejania = ahora;
      if (lejos) for (const m of maquinas) parar(m, "Te has alejado de la plaza: el Auto se ha parado.");
    }

    const pendientesPintar: Maquina[] = [];
    for (const m of maquinas) {
      const visible = ctx.culling.visible(m.malla);
      if (visible && !m.vista) m.sucio = true;
      m.vista = visible;
      const r = m.repro;
      if (r) {
        for (const s of r.pendientes(ahora)) sonar(m, s);
        const idx = r.indice(ahora - r.t0);
        m.zumbido?.ajustar(girando(r.estados[idx]) / 3);
        if (r.hecha(ahora)) terminar(m, ahora);
      } else if (m.auto && !m.ocupada && ahora >= m.siguiente && !document.hidden && !lejos) {
        void girar(m, true);
      }
      if (visible && m.repro) {
        if (m.i === sentada || m.sucio || m.pintado !== m.repro.indice(ahora - m.repro.t0)) pendientesPintar.push(m);
      } else if (visible && m.sucio) pendientesPintar.push(m);
    }
    pendientesPintar.sort((a, b) => (a.i === sentada ? -1 : b.i === sentada ? 1 : a.ultimoPintado - b.ultimoPintado));
    for (const m of pendientesPintar.slice(0, MAX_PINTADAS)) {
      const r = m.repro;
      const t = r ? ahora - r.t0 : 0;
      const idx = r ? r.indice(t) : -1;
      const estado = r ? (m.i === sentada ? r.interpolado(t) : r.estados[idx]) : m.quieto;
      pintor.pintar(estado, m.g);
      m.tex.needsUpdate = true;
      m.sucio = false;
      m.pintado = idx;
      m.ultimoPintado = ahora;
      luces(m, estado, ahora);
    }
    if (botonesSucios || (repintarEn > 0 && ahora >= repintarEn)) {
      repintarEn = 0;
      pintarBotones(ahora);
    }
  };
  ctx.enganchar(tick);
  maquinas.forEach((m) => luces(m, m.quieto, 0));
  pintarBotones(performance.now());

  const jv = (window as unknown as { __jv?: Record<string, unknown> }).__jv;
  if (jv) {
    jv.casino = {
      maquinas,
      sonidos: sonido.registro,
      sonido,
      sentar,
      levantar,
      pulsar: (i: number, k: number) => pulsar(maquinas[i], k),
      girar: (i: number) => pulsar(maquinas[i], 0),
      ultima: () => ultima,
      /** Dónde está de pie quien se acerca a la máquina `i` (a 2,2 m) y hacia dónde ha de mirar. */
      puesto: (i: number) => {
        const f = frenteDe(i);
        return { x: PUESTOS[i].x + f.x * 2.2, z: PUESTOS[i].z + f.z * 2.2, yaw: PUESTOS[i].yaw };
      },
      pantallaDeMaquina: (i: number) => {
        const v = maquinas[i].caja.position.clone().project(ctx.camara3);
        const r = ctx.lienzo.getBoundingClientRect();
        return { x: r.left + ((v.x + 1) / 2) * r.width, y: r.top + ((1 - v.y) / 2) * r.height };
      },
      /** Dónde cae en pantalla un botón (para pulsarlo con el ratón o el dedo en las pruebas). */
      pantallaDe: (i: number, k: number) => {
        const v = new Vector3().setFromMatrixPosition(matrizBoton(i, k, 0)).project(ctx.camara3);
        const r = ctx.lienzo.getBoundingClientRect();
        return { x: r.left + ((v.x + 1) / 2) * r.width, y: r.top + ((1 - v.y) / 2) * r.height };
      },
      estado: () => ({
        sentada,
        bote,
        maquinas: maquinas.map((m) => ({ i: m.i, ocupada: m.ocupada, auto: m.auto, apuesta: m.apuesta, visible: m.vista, repro: m.repro !== null })),
      }),
    };
  }
}
