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
import { Jugador } from "./mundo/jugador";
import { Lugares, fijarZonas } from "./mundo/lugares";
import { fundirRobot } from "./mundo/robot";
import { Remotos } from "./mundo/remotos";
import { COLOR_HORIZONTE, COLOR_SOL, DIR_SOL } from "./mundo/luz";
import { crearMar } from "./mundo/mar";
import { crearMapaProfundidad, crearTerreno, fijarTerreno, heightAt, rellenarMapaProfundidad, sueloEn, type Suelo } from "./mundo/terreno";
import { Obras } from "./mundo/obras";
import { PRUEBAS } from "./mundo/modo";
import { SITIOS_PISTA, pistaDoc } from "./mundo/pista";
import { mundoDeSerie } from "./mundo/serie";
import type { MundoDoc, Teleporte } from "./editor/formato";
import { SITIOS_ISLA, puntoDe, teleportesDeSerie } from "./mundo/teleportes";

export interface Informe {
  meters: number;
  jumps: number;
  slides: number;
  teleports: number;
  places: string[];
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
  /** El mundo que se está dibujando (el de serie, el publicado o el borrador del editor). */
  doc(): MundoDoc;
  /** `f` se llama cada vez que se carga otro mundo (`cargarMundo`), con él. */
  alCargar(f: (doc: MundoDoc) => void): void;
}

export interface Mundo {
  readonly contexto: Contexto;
  /** Cuenta lo andado y los lugares nuevos desde el último informe; vacío si no hay nada que contar. */
  tomarInforme(): Informe | null;
  devolverInforme(i: Informe): void;
  /** Tu pose, para mandarla a la sala. */
  readonly yo: Jugador;
  /** Los demás jugadores, a los que alimenta la sala. */
  readonly remotos: Remotos;
  /** Dibuja el mundo publicado desde el editor en lugar del de serie (o del publicado antes), terreno incluido. */
  cargarMundo(doc: MundoDoc): void;
  /**
   * El editor ha esculpido o pintado la rejilla del terreno (`terreno.ts`) entre esos vértices: se rehace esa parte de la
   * malla. Con `mar`, también el mapa de profundidad del mar (al acabar el trazo; es más caro).
   */
  retocarTerreno(ix0: number, iz0: number, ix1: number, iz1: number, mar: boolean): void;
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
const TESELAS = 4; // el terreno en 4 × 4 baldosas de 80 m (8 × 8 pasaba de 120 llamadas)
const DIST_TERRENO = 480;
const SOMBRA = 32; // semi-lado en metros del recuadro de sombras que sigue al jugador

export async function crearMundo(contenedor: HTMLElement, capa: HTMLElement): Promise<Mundo> {
  const jugador = await Jugador.cargar();
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
  const param = (n: string, defecto: number): number => (debug !== null ? Number(new URLSearchParams(location.search).get(n)) || defecto : defecto);
  let mar: ReturnType<typeof crearMar> | null = null;
  let suelo: Suelo | null = null;
  let profundidad: ReturnType<typeof crearMapaProfundidad> | null = null;
  const obras = new Obras(escena, culling, new MeshLambertMaterial({ vertexColors: true, flatShading: true }));
  let docActual: MundoDoc;
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
    const suelo = new Mesh(new PlaneGeometry(600, 600).rotateX(-Math.PI / 2), new MeshLambertMaterial({ map: rejilla }));
    suelo.receiveShadow = true;
    escena.add(suelo);
    docActual = pistaDoc();
    obras.cargar(docActual);
  } else {
    suelo = crearTerreno(param("teselas", TESELAS), culling, DIST_TERRENO);
    escena.add(...suelo.baldosas);
    profundidad = crearMapaProfundidad();
    mar = crearMar(profundidad);
    escena.add(mar.malla);
    docActual = mundoDeSerie();
    obras.cargar(docActual);
  }
  const cielo = crearCielo();
  escena.add(...cielo.objetos);
  escena.add(jugador.objeto);
  camara.proteger(jugador.objeto);
  camara.enlazarSombra(sol.shadow);
  const remotos = new Remotos(escena, fundirRobot(jugador.modelo, jugador.clips));

  const calidad = new Calidad(renderer, sol, tactil ? 1.5 : 2);
  const ratioInicial = calidad.ratio;
  if (debug === "fijo") calidad.activa = false;
  const controles = new Controles(renderer.domElement, capa);
  const lugares = new Lugares();

  const ajustar = (): void => {
    renderer.setSize(window.innerWidth, window.innerHeight);
    camara3.aspect = window.innerWidth / window.innerHeight;
    // En vertical (móvil) se abre el campo vertical para que el ancho no se quede en un túnel (con el FOV de serie, 55°: hasta 80°).
    const fov = ajustes.fov;
    const anchoMedio = Math.tan((((25 / 55) * fov) * Math.PI) / 180) / camara3.aspect;
    camara3.fov = Math.min(Math.max(80, fov), Math.max(fov, (2 * Math.atan(anchoMedio) * 180) / Math.PI));
    camara3.updateProjectionMatrix();
  };
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
    doc: () => docActual,
    alCargar: (f) => alCargar.push(f),
  };

  let ultimo = performance.now();
  let reloj = 0;
  let sombrasQuitadas = false;
  let metrosFrac = 0;
  let saltosPend = 0;
  let deslizPend = 0;
  let viajesPend = 0;
  let metrosPend = 0;
  let pausado = false;

  const fotograma = (ahora: number): void => {
    if (document.hidden || pausado) {
      ultimo = ahora;
      return;
    }
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
    const saltos0 = jugador.saltos;
    const desliz0 = jugador.deslizamientos;
    const metros0 = jugador.metros;
    jugador.actualizar(dt, {
      dx,
      dz,
      fuerza,
      correr: !quieto && controles.correr,
      saltar: !quieto && controles.saltar,
      agachar: !quieto && controles.agachar,
    });
    controles.saltar = false;
    saltosPend += jugador.saltos - saltos0;
    deslizPend += jugador.deslizamientos - desliz0;
    metrosPend += jugador.metros - metros0;
    if (!PRUEBAS && !jugador.trucos) lugares.comprobar(jugador.x, jugador.y, jugador.z);

    remotos.actualizar(dt, ahora, jugador.x, jugador.z, calidad.sombras);

    camara.actualizar(dt, jugador.x, jugador.y - jugador.bajada, jugador.z);

    // El recuadro de sombras sigue al jugador, ajustado a la rejilla del mapa para que no parpadee.
    const texel = (2 * SOMBRA) / mapa;
    const sx = Math.round(jugador.x / texel) * texel;
    const sz = Math.round(jugador.z / texel) * texel;
    const sy = Math.round(jugador.y / texel) * texel;
    sol.target.position.set(sx, sy, sz);
    sol.position.set(sx + DIR_SOL.x * 80, sy + DIR_SOL.y * 80, sz + DIR_SOL.z * 80);
    sol.target.updateMatrixWorld();

    culling.escala = !calidad.sombras ? 0.6 : calidad.ratio < ratioInicial ? 0.8 : 1;
    culling.actualizar(camara3);
    mar?.actualizar(reloj, camara3.position.x, camara3.position.z);
    cielo.actualizar(dt, camara3.position.x, camara3.position.y, camara3.position.z);
    for (const g of ganchos) g(dt, ahora);

    if (!calidad.sombras && !sombrasQuitadas) {
      sombrasQuitadas = true;
      escena.traverse((o) => {
        const m = (o as Mesh).material;
        if (m) (Array.isArray(m) ? m : [m]).forEach((mm) => (mm.needsUpdate = true));
      });
    }
    renderer.render(escena, camara3);
  };
  renderer.setAnimationLoop(fotograma);

  // Los del panel «Viajar»: en la isla, los del mundo publicado (o los de serie); en el extraplano, los de la pista.
  let teleportes: Teleporte[] = PRUEBAS ? Object.entries(SITIOS_PISTA).map(([nombre, [x, z]]) => ({ nombre, x, z })) : teleportesDeSerie();
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
      lugares,
      heightAt,
      sueloEn,
      info: () => ({ calls: renderer.info.render.calls, triangles: renderer.info.render.triangles }),
      irA,
      tp: (x: number, z: number, yaw?: number, pitch?: number, dist?: number) => {
        jugador.teletransportar(x, z);
        if (yaw !== undefined) camara.yaw = yaw;
        if (pitch !== undefined) camara.pitch = pitch;
        if (dist !== undefined) camara.dist = dist;
      },
    };
  }

  return {
    contexto,
    yo: jugador,
    remotos,
    sitios: Object.keys(PRUEBAS ? SITIOS_PISTA : SITIOS_ISLA),
    irA,
    get teleportes() {
      return teleportes;
    },
    viajar(t) {
      jugador.teletransportar(t.x, t.z);
      if (!PRUEBAS) viajesPend++;
      // Saltar no es pasear: el lugar de destino no cuenta para los logros de esta visita.
      lugares.saltar(jugador.x, jugador.y, jugador.z);
      camara.pitch = 0.25;
      camara.distMax = 16;
      camara.dist = 8;
    },
    tomarInforme() {
      metrosFrac += metrosPend;
      metrosPend = 0;
      const meters = Math.floor(metrosFrac);
      metrosFrac -= meters;
      const jumps = saltosPend;
      saltosPend = 0;
      const slides = deslizPend;
      deslizPend = 0;
      const teleports = viajesPend;
      viajesPend = 0;
      const places = lugares.tomar();
      return meters > 0 || jumps > 0 || slides > 0 || teleports > 0 || places.length > 0 ? { meters, jumps, slides, teleports, places } : null;
    },
    devolverInforme(i) {
      metrosFrac += i.meters;
      saltosPend += i.jumps;
      deslizPend += i.slides;
      viajesPend += i.teleports;
      lugares.devolver(i.places);
    },
    cargarMundo(doc) {
      if (fijarTerreno(doc.terreno)) {
        suelo?.actualizar();
        if (profundidad) rellenarMapaProfundidad(profundidad);
      }
      obras.cargar(doc);
      docActual = doc;
      for (const f of alCargar) f(doc);
      fijarZonas(doc.zonas);
      if (!PRUEBAS) teleportes = doc.teleportes ?? teleportesDeSerie();
    },
    retocarTerreno(ix0, iz0, ix1, iz1, conMar) {
      suelo?.actualizar(ix0, iz0, ix1, iz1);
      if (conMar && profundidad) rellenarMapaProfundidad(profundidad);
    },
    pausar(si) {
      pausado = si;
      if (si) controles.soltarRaton();
    },
    detener() {
      obras.vaciar();
      renderer.setAnimationLoop(null);
      controles.destruir();
      window.removeEventListener("resize", ajustar);
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}

