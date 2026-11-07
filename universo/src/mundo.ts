// Monta el mundo 3D de la isla: escena, luz, cámara, jugador, bucle de dibujo y el informe de paseo para los logros.
import {
  AmbientLight,
  DirectionalLight,
  FogExp2,
  HemisphereLight,
  Mesh,
  MeshLambertMaterial,
  PCFShadowMap,
  PerspectiveCamera,
  Scene,
  WebGLRenderer,
  NeutralToneMapping,
} from "three";
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
import { BAND, CANTERAS, DUNAS, FARO, MUELLE, NUBLO, PICO, PLAZA, crearMapaProfundidad, crearTerreno, heightAt, sueloEn } from "./mundo/terreno";
import { Obras } from "./mundo/obras";
import { mundoDeSerie } from "./mundo/serie";
import type { MundoDoc } from "./editor/formato";

export interface Informe {
  meters: number;
  jumps: number;
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
  tactil: boolean;
  /** `f` se llama cada fotograma, con la cámara y el culling ya al día y antes de dibujar. */
  enganchar(f: (dt: number, ahora: number) => void): void;
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
  /** Dibuja el mundo publicado desde el editor en lugar del de serie (o del publicado antes). */
  cargarMundo(doc: MundoDoc): void;
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
  for (const baldosa of crearTerreno(param("teselas", TESELAS))) {
    escena.add(baldosa);
    culling.registrarMalla(baldosa, DIST_TERRENO);
  }
  const mar = crearMar(crearMapaProfundidad());
  escena.add(mar.malla);
  const cielo = crearCielo();
  escena.add(...cielo.objetos);
  // El pueblo, los hitos y la vegetación son un mundo de datos más: lo publicado desde el editor lo sustituye.
  const obras = new Obras(escena, culling, new MeshLambertMaterial({ vertexColors: true, flatShading: true }));
  obras.cargar(mundoDeSerie());
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
    // En vertical (móvil) se abre el campo vertical para que el ancho no se quede en un túnel.
    const anchoMedio = Math.tan((25 * Math.PI) / 180) / camara3.aspect;
    camara3.fov = Math.min(80, Math.max(55, (2 * Math.atan(anchoMedio) * 180) / Math.PI));
    camara3.updateProjectionMatrix();
  };
  ajustar();
  window.addEventListener("resize", ajustar);

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
    tactil,
    enganchar: (f) => ganchos.push(f),
  };

  let ultimo = performance.now();
  let reloj = 0;
  let sombrasQuitadas = false;
  let metrosFrac = 0;
  let saltosPend = 0;
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
    const metros0 = jugador.metros;
    jugador.actualizar(dt, { dx, dz, fuerza, correr: !quieto && controles.correr, saltar: !quieto && controles.saltar });
    controles.saltar = false;
    saltosPend += jugador.saltos - saltos0;
    metrosPend += jugador.metros - metros0;
    lugares.comprobar(jugador.x, jugador.y, jugador.z);

    remotos.actualizar(dt, ahora, jugador.x, jugador.z, calidad.sombras);

    camara.actualizar(dt, jugador.x, jugador.y, jugador.z);

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
    mar.actualizar(reloj, camara3.position.x, camara3.position.z);
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
      irA: (nombre: string) => {
        // Se coloca a `d` metros del objetivo mirando hacia él, desde el ángulo `a`.
        const sitios: Record<string, [number, number, number, number, number]> = {
          nublo: [NUBLO.x, NUBLO.z, 3.5, 15, 0.2],
          bandama: [BAND.x, BAND.z, 2.2, 17, 0.45],
          dunas: [DUNAS.x - 4, DUNAS.z - 2, 1.4, 14, 0.3],
          faro: [FARO.x, FARO.z, 3.9, 26, 0.12],
          muelle: [MUELLE.cx, MUELLE.cz, 0, 0, 0.3],
          mogan: [MUELLE.x0, MUELLE.z0, 3.0, 18, 0.25],
          canteras: [CANTERAS.x, CANTERAS.z, 1.0, 10, 0.3],
          dunasfaro: [FARO.x, FARO.z, 3.3, 40, 0.2],
          cenit: [PICO.x, PICO.z, 0, 0, 1.45],
          nieves: [PICO.x, PICO.z, 1.0, 6, 0.35],
          drago: [PLAZA.x - 7, PLAZA.z + 4, 1.3, 9, 0.2],
          casino: [PLAZA.x - 0.3, PLAZA.z + 11, -Math.PI / 2, 7, 0.2],
        };
        const [tx, tz, a, d, pitch] = sitios[nombre];
        const px = tx + Math.cos(a) * d;
        const pz = tz + Math.sin(a) * d;
        jugador.teletransportar(px, pz);
        camara.yaw = Math.atan2(-(tx - px), -(tz - pz));
        camara.pitch = pitch;
        camara.distMax = nombre === "cenit" ? 260 : 16;
        camara.dist = nombre === "cenit" ? 230 : ({ dunasfaro: 10, nublo: 12, bandama: 4, faro: 11, dunas: 9 } as Record<string, number>)[nombre] ?? 8;
      },
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
    tomarInforme() {
      metrosFrac += metrosPend;
      metrosPend = 0;
      const meters = Math.floor(metrosFrac);
      metrosFrac -= meters;
      const jumps = saltosPend;
      saltosPend = 0;
      const places = lugares.tomar();
      return meters > 0 || jumps > 0 || places.length > 0 ? { meters, jumps, places } : null;
    },
    devolverInforme(i) {
      metrosFrac += i.meters;
      saltosPend += i.jumps;
      lugares.devolver(i.places);
    },
    cargarMundo(doc) {
      obras.cargar(doc);
      fijarZonas(doc.zonas);
    },
    pausar(si) {
      pausado = si;
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

