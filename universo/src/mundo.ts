// Monta el mundo 3D de la isla: escena, luz, cámara, jugador, bucle de dibujo y el informe de paseo para los logros.
import {
  AmbientLight,
  CanvasTexture,
  DirectionalLight,
  FogExp2,
  HemisphereLight,
  Mesh,
  MeshLambertMaterial,
  PCFShadowMap,
  PerspectiveCamera,
  PlaneGeometry,
  RepeatWrapping,
  SRGBColorSpace,
  Scene,
  WebGLRenderer,
  NeutralToneMapping,
} from "three";
import { ajustes, iniciarAjustes } from "./mundo/ajustes";
import { Calidad } from "./mundo/calidad";
import { Camara } from "./mundo/camara";
import { Controles } from "./mundo/controles";
import { crearCielo } from "./mundo/cielo";
import { Culling } from "./mundo/culling";
import { Jugador, VEL_CORRER, VEL_MAX, type Tecnica } from "./mundo/jugador";
import { fundirRobot } from "./mundo/robot";
import { Remotos } from "./mundo/remotos";
import { COLOR_HORIZONTE, COLOR_SOL, DIR_SOL } from "./mundo/luz";
import { crearMar } from "./mundo/mar";
import { crearMapaProfundidad, heightAt, rellenarMapaProfundidad, sueloEn } from "./mundo/terreno";
import { Suelo } from "./mundo/suelo";
import { Cercania, type Fuente, fuenteLocal } from "./mundo/cercania";
import { Obras } from "./mundo/obras";
import { PRUEBAS } from "./mundo/modo";
import { SITIOS_PISTA, pistaDoc } from "./mundo/pista";
import { mundoDeSerie } from "./mundo/serie";
import type { MundoDoc, Teleporte } from "./editor/formato";
import { SITIOS_ISLA, puntoDe, teleportesDeSerie } from "./mundo/teleportes";

/** Lo del paseo que la sala no ve en las poses (los metros, saltos, deslizamientos y lugares los cuenta ella). */
export interface Informe extends Tecnica {
  teleports: number;
}

/** Lo que necesita un módulo que se cuelga del mundo (el casino): la escena, la cámara, el culling y un gancho por fotograma. */
export interface Contexto {
  escena: Scene;
  /** El dibujante del mundo: el editor de niveles pinta con él, en su canvas, mientras el bucle está en pausa. */
  renderer: WebGLRenderer;
  camara: Camara;
  camara3: PerspectiveCamera;
  culling: Culling;
  lienzo: HTMLCanvasElement;
  capa: HTMLElement;
  yo: Jugador;
  /** Resolución y sombras adaptativas (las devtools enseñan cómo van). */
  calidad: Calidad;
  tactil: boolean;
  /** `f` se llama cada fotograma, con la cámara y el culling ya al día y antes de dibujar. */
  enganchar(f: (dt: number, ahora: number) => void): void;
  /** Lo que se está dibujando (del de serie, del publicado o del borrador del editor): solo las celdas cargadas, las de alrededor. */
  doc(): MundoDoc;
  /** `f` se llama cada vez que cambia lo cargado (otro mundo, o celdas que llegan y se van al moverse), con el documento nuevo. */
  alCargar(f: (doc: MundoDoc) => void): void;
}

export interface Mundo {
  readonly contexto: Contexto;
  /** Cuenta los teleportes y los trucos desde el último informe; vacío si no hay nada que contar. */
  tomarInforme(): Informe | null;
  devolverInforme(i: Informe): void;
  /** Tu pose, para mandarla a la sala. */
  readonly yo: Jugador;
  /** Los demás jugadores, a los que alimenta la sala. */
  readonly remotos: Remotos;
  /**
   * Dibuja un mundo que se tiene entero (el borrador del editor, las devtools) en lugar del que había, terreno incluido. Se carga por
   * cercanía como el publicado; si viene de otro `cargarMundo`, solo se rehacen las celdas que han cambiado.
   */
  cargarMundo(doc: MundoDoc): void;
  /** Dibuja el mundo publicado de esa fuente; si ya se veía otra versión, solo se vuelven a bajar los trozos cercanos que han cambiado. */
  cargarPublicado(f: Fuente): void;
  /** Vuelve al mundo de serie (nunca se ha publicado nada). */
  cargarSerie(): void;
  /** `f` se llama si el servidor ya no tiene las celdas del índice que se está usando: hay que pedir otro (`cargarPublicado`). */
  alCaducar(f: () => void): void;
  /**
   * Carga y monta lo que hay alrededor de (x, z) en este fotograma (el editor, que pinta él mientras el bucle está en pausa). Dice si
   * queda algo por montar: entonces hay que volver a llamar en el siguiente.
   */
  enfocar(x: number, z: number): boolean;
  /**
   * El editor ha esculpido o pintado las rejillas de las celdas (`rejillas` de `terreno.ts`) entre esos vértices de la rejilla del
   * mundo (cada 2 m desde 0): se rehace esa parte de la malla. Con `mar`, también el mapa de profundidad del mar (al acabar el
   * trazo; es más caro).
   */
  retocarTerreno(gx0: number, gz0: number, gx1: number, gz1: number, mar: boolean): void;
  /** Sitios a los que se puede ir con `irA`: los de la isla o, en el extraplano, los de la pista. */
  readonly sitios: string[];
  /** Te lleva a uno de `sitios`, mirándolo. */
  irA(nombre: string): void;
  /** Los sitios del panel «Viajar» del menú. */
  readonly teleportes: Teleporte[];
  /** Te lleva a un teleporte, a ras de suelo. */
  viajar(t: Teleporte): void;
  /** Con `true` el bucle no pinta ni simula (el editor está encima); con `false` sigue. */
  pausar(si: boolean): void;
  detener(): void;
}

// Tamaños medidos con docs/medir_universo.py (los de obras, en mundo/medidas.ts); con ?debug=1&teselas=.. se prueban otros.
const DIST_CARGA = 480; // m alrededor de la cámara cuyas celdas se cargan enteras (con calidad baja, menos: `radioCarga`)
/** Lo que puede gastar cada fotograma en montar lo que llega (ms); al entrar, con la pantalla de carga, mucho más. */
const PRESUPUESTO_MS = 4;
const ESPERA_ENTRADA_MS = 6000;
/**
 * Presupuesto de la Biblia por fotograma (sombras incluidas). En un sitio muy denso se acorta la distancia de dibujo de lo que no es
 * suelo hasta que cabe (`alcance`), y se devuelve poco a poco cuando sobra: lo lejano desaparece antes, pero nunca se pasa.
 */
const MAX_LLAMADAS = 120;
const MAX_TRIANGULOS = 250_000;
const MIN_ALCANCE = 0.25;
const SOMBRA = 32; // semi-lado en metros del recuadro de sombras que sigue al jugador

/**
 * Monta el mundo una sola vez: con el publicado que traiga `inicial` (se pide a la vez que el login) o, si da `null`, con
 * el de serie. En el extraplano no se mira.
 */
export async function crearMundo(contenedor: HTMLElement, capa: HTMLElement, inicial: Promise<Fuente | null>): Promise<Mundo> {
  const [jugador, publicado] = await Promise.all([Jugador.cargar(), inicial]);
  const tactil = window.matchMedia("(pointer: coarse)").matches;
  const debug = new URLSearchParams(location.search).get("debug");

  const renderer = new WebGLRenderer({ antialias: !tactil, powerPreference: "high-performance" });
  renderer.toneMapping = NeutralToneMapping;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = PCFShadowMap;
  contenedor.append(renderer.domElement);

  const escena = new Scene();
  escena.background = COLOR_HORIZONTE;
  escena.fog = new FogExp2(COLOR_HORIZONTE.getHex(), 0.0028);

  const camara3 = new PerspectiveCamera(55, 1, 0.3, 1600);
  const camara = new Camara(camara3);

  // Luces: sol cálido con sombras + hemisférica de relleno.
  const sol = new DirectionalLight(COLOR_SOL, 3.2);
  sol.castShadow = true;
  const mapa = tactil ? 1024 : 2048;
  sol.shadow.mapSize.set(mapa, mapa);
  sol.shadow.camera.left = -SOMBRA;
  sol.shadow.camera.right = SOMBRA;
  sol.shadow.camera.top = SOMBRA;
  sol.shadow.camera.bottom = -SOMBRA;
  sol.shadow.camera.near = 1;
  sol.shadow.camera.far = 160;
  sol.shadow.bias = -0.0006;
  sol.shadow.normalBias = 0.06;
  escena.add(sol, sol.target);
  escena.add(new HemisphereLight(0xbcd6ff, 0xc89a70, 1.9));
  escena.add(new AmbientLight(0xffe8d0, 0.25));

  // Mundo.
  const culling = new Culling();
  let mar: ReturnType<typeof crearMar> | null = null;
  let suelo: Suelo | null = null;
  let profundidad: ReturnType<typeof crearMapaProfundidad> | null = null;
  const obras = new Obras(escena, culling, new MeshLambertMaterial({ vertexColors: true, flatShading: true }));
  const alCargar: ((doc: MundoDoc) => void)[] = [];
  if (PRUEBAS) {
    // El extraplano: un suelo gris a 0 m con cuadrícula de 1 m (y de 10 m más marcada) pintada en una textura que se
    // repite (una sola llamada, sin parpadeo de líneas a lo lejos), y la pista, como datos.
    const lienzo = document.createElement("canvas");
    lienzo.width = lienzo.height = 512; // una baldosa de 10 m
    const g = lienzo.getContext("2d") as CanvasRenderingContext2D;
    g.fillStyle = "#9aa3ab";
    g.fillRect(0, 0, 512, 512);
    g.fillStyle = "#7b848c";
    for (let i = 1; i < 10; i++) {
      g.fillRect(Math.round(i * 51.2) - 1, 0, 2, 512);
      g.fillRect(0, Math.round(i * 51.2) - 1, 512, 2);
    }
    g.fillStyle = "#4a535b";
    g.fillRect(0, 0, 4, 512);
    g.fillRect(0, 0, 512, 4);
    const rejilla = new CanvasTexture(lienzo);
    rejilla.wrapS = rejilla.wrapT = RepeatWrapping;
    rejilla.repeat.set(60, 60);
    rejilla.anisotropy = renderer.capabilities.getMaxAnisotropy();
    rejilla.colorSpace = SRGBColorSpace;
    const plano = new Mesh(new PlaneGeometry(600, 600).rotateX(-Math.PI / 2), new MeshLambertMaterial({ map: rejilla }));
    plano.receiveShadow = true;
    escena.add(plano);
  } else {
    suelo = new Suelo(escena, culling, new MeshLambertMaterial({ vertexColors: true, flatShading: true }));
    profundidad = crearMapaProfundidad();
    mar = crearMar(profundidad);
    escena.add(mar.malla);
  }
  const cercania = new Cercania(obras, suelo);
  // El suelo de la isla del centro ha cambiado: el mapa de profundidad del mar se rehace (como mucho dos veces por segundo).
  let marSucio = 0;
  if (suelo) suelo.alCambiarIsla = () => (marSucio ||= performance.now() + 500);
  cercania.cambiarFuente(PRUEBAS ? fuenteLocal(pistaDoc()) : (publicado ?? fuenteLocal(mundoDeSerie())));
  // Lo de alrededor del jugador se baja y se monta antes de quitar la pantalla de carga (sin esperar más de `ESPERA_ENTRADA_MS`).
  const hastaEntrada = performance.now() + ESPERA_ENTRADA_MS;
  for (;;) {
    cercania.actualizar(jugador.x, jugador.z);
    if (!cercania.trabajar(performance.now() + 40) || performance.now() > hastaEntrada) break;
    await new Promise((r) => setTimeout(r, 0));
  }
  const cielo = crearCielo();
  escena.add(...cielo.objetos);
  escena.add(jugador.objeto, jugador.cuerda);
  camara.proteger(jugador.objeto);
  camara.enlazarSombra(sol.shadow);
  const remotos = new Remotos(escena, fundirRobot(jugador.modelo, jugador.clips));

  if (profundidad) rellenarMapaProfundidad(profundidad);
  marSucio = 0;
  const calidad = new Calidad(renderer, sol, tactil ? 1.5 : 2);
  const ratioInicial = calidad.ratio;
  if (debug === "fijo") calidad.activa = false;
  const controles = new Controles(renderer.domElement, capa);

  const ajustar = (): void => {
    renderer.setSize(window.innerWidth, window.innerHeight);
    camara3.aspect = window.innerWidth / window.innerHeight;
    // En vertical (móvil) se abre el campo vertical para que el ancho no se quede en un túnel (con el FOV de serie, 55°: hasta 80°).
    const fov = ajustes.fov;
    const anchoMedio = Math.tan((((25 / 55) * fov) * Math.PI) / 180) / camara3.aspect;
    fovBase = Math.min(Math.max(80, fov), Math.max(fov, (2 * Math.atan(anchoMedio) * 180) / Math.PI));
    camara3.fov = fovBase + fovExtra;
    camara3.updateProjectionMatrix();
  };
  let fovBase = 55;
  let fovExtra = 0;
  ajustar();
  window.addEventListener("resize", ajustar);
  iniciarAjustes(capa, tactil, ajustar);

  const ganchos: ((dt: number, ahora: number) => void)[] = [];
  const contexto: Contexto = {
    escena,
    renderer,
    camara,
    camara3,
    culling,
    lienzo: renderer.domElement,
    capa,
    yo: jugador,
    calidad,
    tactil,
    enganchar: (f) => ganchos.push(f),
    doc: () => cercania.doc(),
    alCargar: (f) => alCargar.push(f),
  };

  let ultimo = performance.now();
  let reloj = 0;
  let conSombras = true;
  let viajesPend = 0;
  let pausado = false;

  let detenido = false;
  /** Si en el último fotograma quedó algo por bajar o montar alrededor (las medidas esperan a que no). */
  let ocupado = false;
  let escalaCalidad = 1;
  /** Lo que se acorta la distancia de dibujo para no pasar del presupuesto (1: nada). */
  let alcance = 1;
  const fotograma = (ahora: number): void => {
    if (document.hidden || pausado) {
      ultimo = ahora;
      detenido = true;
      return;
    }
    if (detenido) calidad.callar(3); // al volver (pestaña, editor) se recompila y se sube lo que cambió: tirón
    detenido = false;
    const real = (ahora - ultimo) / 1000;
    ultimo = ahora;
    const dt = Math.min(real, 0.1);
    reloj += dt;
    calidad.medir(real);

    controles.libre = camara.anclada; // sentado ante una máquina hace falta el cursor para los botones
    controles.actualizar();
    const cam = controles.consumirCamara();
    if (controles.cambiarVista) {
      controles.cambiarVista = false;
      camara.alternar();
    }
    camara.girar(cam.dYaw, cam.dPitch, cam.zoom);
    const f = camara.adelante;
    // Anclada (sentado ante una máquina) el jugador no se mueve.
    const quieto = camara.anclada;
    const mx = quieto ? 0 : controles.mover.x;
    const my = quieto ? 0 : controles.mover.y;
    const fuerza = Math.hypot(mx, my);
    // Hacia delante = f; a la derecha = (-f.z, f.x).
    let dx = f.x * my - f.z * mx;
    let dz = f.z * my + f.x * mx;
    if (fuerza > 0) {
      dx /= fuerza;
      dz /= fuerza;
    }
    if (controles.gancho) {
      controles.gancho = false;
      const d = camara.apuntar();
      if (!quieto) jugador.lanzarGancho(d.x, d.y, d.z);
    }
    jugador.actualizar(dt, {
      dx,
      dz,
      fuerza,
      correr: !quieto && controles.correr,
      saltar: !quieto && controles.saltar,
      agachar: !quieto && controles.agachar,
    });
    controles.saltar = false;

    remotos.actualizar(dt, ahora, jugador.x, jugador.z, calidad.sombras);

    // Corriendo por una pared la cámara se ladea hacia fuera, y con la velocidad se abre el campo de visión (hasta 12°).
    const pared = jugador.pared;
    const der = camara.derecha;
    camara.inclinar = pared ? -0.12 * (pared.x * der.x + pared.z * der.z) : 0;
    const extra = 12 * Math.min(1, Math.max(0, jugador.velH - VEL_CORRER) / (VEL_MAX - VEL_CORRER));
    fovExtra += (extra - fovExtra) * (1 - Math.exp(-dt * 4));
    if (Math.abs(camara3.fov - fovBase - fovExtra) > 0.05) {
      camara3.fov = fovBase + fovExtra;
      camara3.updateProjectionMatrix();
    }
    camara.actualizar(dt, jugador.x, jugador.y - jugador.bajada, jugador.z);

    // El recuadro de sombras sigue al jugador, ajustado a la rejilla del mapa para que no parpadee.
    const texel = (2 * SOMBRA) / mapa;
    const sx = Math.round(jugador.x / texel) * texel;
    const sz = Math.round(jugador.z / texel) * texel;
    const sy = Math.round(jugador.y / texel) * texel;
    sol.target.position.set(sx, sy, sz);
    sol.position.set(sx + DIR_SOL.x * 80, sy + DIR_SOL.y * 80, sz + DIR_SOL.z * 80);
    sol.target.updateMatrixWorld();

    escalaCalidad = !calidad.sombras ? 0.6 : calidad.ratio < ratioInicial ? 0.8 : 1;
    culling.escala = escalaCalidad * alcance;
    cercania.radio = radioCarga();
    cercania.actualizar(camara3.position.x, camara3.position.z);
    ocupado = cercania.trabajar(performance.now() + PRESUPUESTO_MS);
    if (marSucio && ahora > marSucio && profundidad) {
      marSucio = 0;
      rellenarMapaProfundidad(profundidad);
    }
    culling.actualizar(camara3);
    mar?.actualizar(reloj, camara3.position.x, camara3.position.z);
    cielo.actualizar(dt, camara3.position.x, camara3.position.y, camara3.position.z);
    for (const g of ganchos) g(dt, ahora);

    if (calidad.sombras !== conSombras) {
      conSombras = calidad.sombras; // quitarlas o devolverlas recompila los materiales
      escena.traverse((o) => {
        const m = (o as Mesh).material;
        if (m) (Array.isArray(m) ? m : [m]).forEach((mm) => (mm.needsUpdate = true));
      });
    }
    renderer.render(escena, camara3);
    const info = renderer.info.render;
    if (info.calls > 0.92 * MAX_LLAMADAS || info.triangles > 0.92 * MAX_TRIANGULOS) alcance = Math.max(MIN_ALCANCE, alcance * 0.95);
    else if (info.calls < 0.75 * MAX_LLAMADAS && info.triangles < 0.75 * MAX_TRIANGULOS) alcance = Math.min(1, alcance * 1.01);
  };
  renderer.setAnimationLoop(fotograma);

  // Los del panel «Viajar»: en la isla, los del mundo publicado (o los de serie); en el extraplano, los de la pista.
  const teleportesPista = Object.entries(SITIOS_PISTA).map(([nombre, [x, z]]) => ({ nombre, x, z }));
  const teleportes = (): Teleporte[] => (PRUEBAS ? teleportesPista : (cercania.indice?.teleportes ?? teleportesDeSerie()));
  const irA = (nombre: string): void => {
    // En el extraplano, la pista se mira hacia -z; el casino está donde en la isla y se encuadra igual.
    if (PRUEBAS && nombre !== "casino") {
      const [x, z] = SITIOS_PISTA[nombre] ?? SITIOS_PISTA.plaza;
      jugador.teletransportar(x, z);
      camara.yaw = 0;
      camara.pitch = 0.3;
      camara.dist = 9;
      return;
    }
    if (!SITIOS_ISLA[nombre]) return;
    const [tx, tz, , , pitch] = SITIOS_ISLA[nombre];
    const [px, pz] = puntoDe(nombre);
    jugador.teletransportar(px, pz);
    camara.yaw = Math.atan2(-(tx - px), -(tz - pz));
    camara.pitch = pitch;
    camara.distMax = nombre === "cenit" ? 260 : 16;
    camara.dist = nombre === "cenit" ? 230 : ({ dunasfaro: 10, nublo: 12, bandama: 4, faro: 11, dunas: 9 } as Record<string, number>)[nombre] ?? 8;
  };

  if (debug !== null) {
    (window as unknown as Record<string, unknown>).__jv = {
      renderer,
      escena,
      remotos,
      jugador,
      gesto: (id: number) => jugador.gesto(id),
      camara,
      calidad,
      culling,
      controles,
      heightAt,
      sueloEn,
      info: () => ({ calls: renderer.info.render.calls, triangles: renderer.info.render.triangles }),
      cercania,
      cargado: () => ({ ...cercania.estado, obras: obras.mallas, suelo: suelo?.mallas ?? 0, ocupado, alcance: Math.round(alcance * 100) / 100 }),
      irA,
      tp: (x: number, z: number, yaw?: number, pitch?: number, dist?: number) => {
        jugador.teletransportar(x, z);
        if (yaw !== undefined) camara.yaw = yaw;
        if (pitch !== undefined) camara.pitch = pitch;
        if (dist !== undefined) camara.dist = dist;
      },
    };
  }

  // Lo cargado cambia muy a menudo mientras se camina: quien mira el documento (el casino) se entera como mucho cuatro veces por segundo.
  let avisar = 0;
  cercania.alCambiar = () => (avisar ||= performance.now() + 250);
  ganchos.push((_dt, ahora) => {
    if (!avisar || ahora < avisar) return;
    avisar = 0;
    const doc = cercania.doc();
    for (const f of alCargar) f(doc);
  });
  /** Pasa a otra fuente: lo de antes se queda hasta que llega lo nuevo. */
  const cambiar = (f: Fuente): void => {
    cercania.cambiarFuente(f);
    calidad.callar(4); // montar el mundo da un tirón que no dice nada de los fps
  };
  let local: Fuente | null = null;
  const radioCarga = (): number => DIST_CARGA * Math.max(escalaCalidad, 0.85);

  return {
    contexto,
    yo: jugador,
    remotos,
    sitios: Object.keys(PRUEBAS ? SITIOS_PISTA : SITIOS_ISLA),
    irA,
    get teleportes() {
      return teleportes();
    },
    viajar(t) {
      jugador.teletransportar(t.x, t.z);
      if (!PRUEBAS) viajesPend++;
      camara.pitch = 0.25;
      camara.distMax = 16;
      camara.dist = 8;
    },
    tomarInforme() {
      const teleports = viajesPend;
      viajesPend = 0;
      const t = jugador.tecnica;
      const tecnica: Tecnica = { ...t, speed: Math.floor(t.speed) };
      t.wallruns = t.walljumps = t.doublejumps = t.grapples = t.speed = t.chain = 0;
      const algo = teleports > 0 || Object.values(tecnica).some((v) => v > 0);
      return algo ? { teleports, ...tecnica } : null;
    },
    devolverInforme(i) {
      viajesPend += i.teleports;
      const t = jugador.tecnica;
      t.wallruns += i.wallruns;
      t.walljumps += i.walljumps;
      t.doublejumps += i.doublejumps;
      t.grapples += i.grapples;
      t.speed = Math.max(t.speed, i.speed);
      t.chain = Math.max(t.chain, i.chain);
    },
    cargarMundo(doc) {
      local = fuenteLocal(doc, local);
      cambiar(local);
    },
    cargarPublicado(f) {
      local = null;
      cambiar(f);
    },
    cargarSerie() {
      local = null;
      cambiar(fuenteLocal(mundoDeSerie()));
    },
    alCaducar(f) {
      cercania.alCaducar = f;
    },
    enfocar(x, z) {
      cercania.radio = radioCarga();
      cercania.actualizar(x, z);
      const queda = cercania.trabajar(performance.now() + 8);
      if (avisar) {
        avisar = 0;
        const doc = cercania.doc();
        for (const f of alCargar) f(doc);
      }
      return queda;
    },
    retocarTerreno(ix0, iz0, ix1, iz1, conMar) {
      suelo?.retocar(ix0, iz0, ix1, iz1);
      if (conMar && profundidad) rellenarMapaProfundidad(profundidad);
    },
    pausar(si) {
      pausado = si;
      if (si) controles.soltarRaton();
    },
    detener() {
      cercania.vaciar();
      suelo?.vaciar();
      obras.vaciar();
      renderer.setAnimationLoop(null);
      controles.destruir();
      window.removeEventListener("resize", ajustar);
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}

