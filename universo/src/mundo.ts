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
import { fundir } from "./mundo/geom";
import { crearCanteras, crearFaro, crearMogan, crearMuelle, crearRoques } from "./mundo/hitos";
import { Jugador } from "./mundo/jugador";
import { Lugares } from "./mundo/lugares";
import { COLOR_HORIZONTE, COLOR_SOL, DIR_SOL } from "./mundo/luz";
import { crearMar } from "./mundo/mar";
import { crearPueblo } from "./mundo/pueblo";
import { BAND, CANTERAS, DUNAS, FARO, MUELLE, NUBLO, PICO, PLAZA, crearMapaProfundidad, crearTerreno, heightAt, sueloEn } from "./mundo/terreno";
import { crearVegetacion } from "./mundo/vegetacion";

export interface Informe {
  meters: number;
  jumps: number;
  places: string[];
}

export interface Mundo {
  /** Cuenta lo andado y los lugares nuevos desde el último informe; vacío si no hay nada que contar. */
  tomarInforme(): Informe | null;
  devolverInforme(i: Informe): void;
  detener(): void;
}

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
  const terreno = crearTerreno();
  escena.add(terreno);
  const mar = crearMar(crearMapaProfundidad());
  escena.add(mar.malla);
  const cielo = crearCielo();
  escena.add(...cielo.objetos);
  for (const v of crearVegetacion()) escena.add(v);
  const construcciones = new Mesh(
    fundir([...crearPueblo(), ...crearFaro(), ...crearMuelle(), ...crearRoques(), ...crearCanteras(), ...crearMogan()]),
    new MeshLambertMaterial({ vertexColors: true, flatShading: true }),
  );
  construcciones.castShadow = true;
  construcciones.receiveShadow = true;
  escena.add(construcciones);
  escena.add(jugador.objeto);

  const calidad = new Calidad(renderer, sol, tactil ? 1.5 : 2);
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

  let ultimo = performance.now();
  let reloj = 0;
  let sombrasQuitadas = false;
  let metrosFrac = 0;
  let saltosPend = 0;
  let metrosPend = 0;

  const fotograma = (ahora: number): void => {
    if (document.hidden) {
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
    camara.girar(cam.dYaw, cam.dPitch, cam.zoom);
    const f = camara.adelante;
    const mx = controles.mover.x;
    const my = controles.mover.y;
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
    jugador.actualizar(dt, { dx, dz, fuerza, correr: controles.correr, saltar: controles.saltar });
    controles.saltar = false;
    saltosPend += jugador.saltos - saltos0;
    metrosPend += jugador.metros - metros0;
    lugares.comprobar(jugador.x, jugador.y, jugador.z);

    camara.actualizar(dt, jugador.x, jugador.y, jugador.z);

    // El recuadro de sombras sigue al jugador, ajustado a la rejilla del mapa para que no parpadee.
    const texel = (2 * SOMBRA) / mapa;
    const sx = Math.round(jugador.x / texel) * texel;
    const sz = Math.round(jugador.z / texel) * texel;
    const sy = Math.round(jugador.y / texel) * texel;
    sol.target.position.set(sx, sy, sz);
    sol.position.set(sx + DIR_SOL.x * 80, sy + DIR_SOL.y * 80, sz + DIR_SOL.z * 80);
    sol.target.updateMatrixWorld();

    mar.actualizar(reloj, camara3.position.x, camara3.position.z);
    cielo.actualizar(dt, camara3.position.x, camara3.position.y, camara3.position.z);

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

  if (debug) {
    (window as unknown as Record<string, unknown>).__jv = {
      renderer,
      jugador,
      camara,
      calidad,
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
    detener() {
      renderer.setAnimationLoop(null);
      controles.destruir();
      window.removeEventListener("resize", ajustar);
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}

