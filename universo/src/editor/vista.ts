// Vista 3D del editor de assets: su propio renderer y escena, que se pinta solo cuando algo cambia (nunca en bucle).
import {
  AmbientLight,
  Box3,
  BufferGeometry,
  Color,
  DirectionalLight,
  GridHelper,
  Group,
  HemisphereLight,
  Line,
  LineBasicMaterial,
  LineLoop,
  Mesh,
  MeshLambertMaterial,
  NeutralToneMapping,
  Object3D,
  PerspectiveCamera,
  Raycaster,
  Scene,
  Vector2,
  Vector3,
  WebGLRenderer,
} from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { COLOR_HORIZONTE, COLOR_SOL, DIR_SOL } from "../mundo/luz";
import { ArrastreGrupo, Recuadro, type Transformacion } from "./grupo";
import { type Circulo, geometriaForma, geometriaMalla, type Malla, type Pieza } from "./formato";

export type Modo = "translate" | "rotate" | "scale";

const GRADOS = Math.PI / 180;

export class Vista {
  readonly renderer = new WebGLRenderer({ antialias: true });
  /** Se llama en cada paso del arrastre de lo elegido y, con `fin`, al soltarlo; las transformaciones se leen con `transformacion`. */
  alTransformar: (fin: boolean) => void = () => {};
  /** Clic sobre una pieza (o sobre nada: -1); `aditivo` si se pulsaba Ctrl o Cmd. */
  alElegir: (i: number, aditivo: boolean) => void = () => {};
  /** Caja soltada: las piezas visibles cuyo centro cae dentro; `aditivo` si se pulsaba Ctrl o Cmd. */
  alCaja: (indices: number[], aditivo: boolean) => void = () => {};

  private readonly escena = new Scene();
  private readonly camara = new PerspectiveCamera(50, 1, 0.05, 500);
  private readonly orbita: OrbitControls;
  private readonly transform: TransformControls;
  private readonly grupo = new Group();
  private readonly anillos = new Group();
  private readonly circulo: BufferGeometry;
  private readonly lineaCirculo = new LineBasicMaterial({ color: 0xffd86b });
  private readonly geometrias = new Map<string, BufferGeometry>();
  private malla: { datos: Malla; mesh: Mesh } | null = null;
  private meshes: Mesh[] = [];
  private modelo: Object3D | null = null;
  /** Con varias piezas elegidas el gizmo va enganchado a este pivote, en el centro de su caja. */
  private readonly pivote = new Object3D();
  private arrastre: ArrastreGrupo | null = null;
  private elegidas: number[] = [];
  private principal = -1;
  private local = false;
  private conAjuste = false;
  private invertido = false;
  private readonly recuadro: Recuadro;
  private pendiente = 0;
  private cerrada = false;
  private readonly observador: ResizeObserver;
  private readonly inicio = new Vector2();

  constructor(private readonly contenedor: HTMLElement) {
    const r = this.renderer;
    r.toneMapping = NeutralToneMapping;
    contenedor.append(r.domElement);
    this.escena.background = new Color(0x3a4352);
    const sol = new DirectionalLight(COLOR_SOL, 3.2);
    sol.position.copy(DIR_SOL).multiplyScalar(10);
    this.escena.add(sol, new HemisphereLight(COLOR_HORIZONTE, 0x4a4a55, 1.1), new AmbientLight(0xffffff, 0.35), this.grupo);
    this.escena.add(new GridHelper(20, 20, 0x8890a0, 0x58606e));

    const puntos = Array.from({ length: 64 }, (_, i) => new Vector3(Math.cos((i / 64) * 2 * Math.PI), 0.01, Math.sin((i / 64) * 2 * Math.PI)));
    this.circulo = new BufferGeometry().setFromPoints(puntos);
    this.escena.add(this.anillos);

    this.camara.position.set(3, 2.5, 4);
    this.orbita = new OrbitControls(this.camara, r.domElement);
    this.orbita.target.set(0, 0.5, 0);
    this.orbita.addEventListener("change", () => this.pedir());

    this.transform = new TransformControls(this.camara, r.domElement);
    this.transform.addEventListener("change", () => this.pedir());
    this.transform.addEventListener("dragging-changed", (e) => {
      this.orbita.enabled = !e.value;
      this.arrastre = e.value && this.elegidas.length > 1 ? new ArrastreGrupo(this.pivote, this.elegidas.map((i) => this.transformacion(i))) : null;
      if (!e.value) this.alTransformar(true);
    });
    this.transform.addEventListener("objectChange", () => {
      if (this.arrastre) {
        const ts = this.arrastre.ahora();
        this.elegidas.forEach((i, k) => this.poner(this.meshes[i], ts[k]));
      }
      this.alTransformar(false);
    });
    this.escena.add(this.transform.getHelper(), this.pivote);
    this.recuadro = new Recuadro(contenedor, (rect, aditivo) => this.alCaja(this.dentro(rect), aditivo));
    window.addEventListener("pointerdown", this.alPuntero, true);
    window.addEventListener("pointermove", this.alPuntero, true);

    r.domElement.addEventListener("pointerdown", this.alBajar);
    r.domElement.addEventListener("pointerup", this.alSubir);
    this.observador = new ResizeObserver(() => this.ajustar());
    this.observador.observe(contenedor);
    this.ajustar();
  }

  /** Pide un fotograma; varias peticiones seguidas pintan una sola vez. */
  pedir(): void {
    if (this.pendiente || this.cerrada) return;
    this.pendiente = requestAnimationFrame(() => {
      this.pendiente = 0;
      this.renderer.render(this.escena, this.camara);
    });
  }

  private ajustar(): void {
    const { clientWidth: w, clientHeight: h } = this.contenedor;
    if (!w || !h) return;
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.setSize(w, h);
    this.camara.aspect = w / h;
    this.camara.updateProjectionMatrix();
    this.pedir();
  }

  private geometria(p: Pieza): BufferGeometry {
    const k = p.forma === "prisma" ? `prisma:${p.lados}:${p.arriba}:${p.abajo}:${p.fase}` : p.forma;
    let g = this.geometrias.get(k);
    if (!g) this.geometrias.set(k, (g = geometriaForma(p)));
    return g;
  }

  /** La malla hecha de un asset: con sus colores por vértice y sin entrar en `meshes`, así que el ratón no la elige. */
  private ponerMalla(datos: Malla | undefined): void {
    if (this.malla && this.malla.datos !== datos) {
      this.grupo.remove(this.malla.mesh);
      this.malla.mesh.geometry.dispose();
      (this.malla.mesh.material as MeshLambertMaterial).dispose();
      this.malla = null;
    }
    if (datos && !this.malla) {
      const mesh = new Mesh(geometriaMalla(datos), new MeshLambertMaterial({ flatShading: true, vertexColors: true }));
      this.grupo.add(mesh);
      this.malla = { datos, mesh };
    }
  }

  /** Pone las piezas en la escena: si hay las mismas que antes se actualizan, si no se rehacen. */
  mostrarPiezas(piezas: Pieza[], elegidas: number[], principal: number, ocultas: boolean[], malla?: Malla): void {
    this.quitarModelo();
    this.ponerMalla(malla);
    if (this.meshes.length !== piezas.length) {
      this.transform.detach();
      this.vaciar();
      this.meshes = piezas.map(() => new Mesh(undefined, new MeshLambertMaterial({ flatShading: true })));
      this.grupo.add(...this.meshes);
    }
    piezas.forEach((p, i) => {
      const m = this.meshes[i];
      m.geometry = this.geometria(p);
      (m.material as MeshLambertMaterial).color.setHex(p.color);
      m.position.set(...p.pos);
      m.rotation.set(p.rot[0], p.rot[1], p.rot[2], "YXZ");
      m.scale.set(...p.esc);
      m.visible = !ocultas[i];
    });
    this.elegir(elegidas, principal);
  }

  mostrarModelo(modelo: Object3D): void {
    this.transform.detach();
    this.vaciar();
    this.ponerMalla(undefined);
    this.quitarModelo();
    this.modelo = modelo.clone(true);
    this.grupo.add(this.modelo);
    this.pedir();
  }

  /** Deja la escena sin asset. */
  limpiar(): void {
    this.transform.detach();
    this.vaciar();
    this.ponerMalla(undefined);
    this.quitarModelo();
    this.pedir();
  }

  private quitarModelo(): void {
    if (this.modelo) this.grupo.remove(this.modelo); // su geometría es de la caché de GLB: no se libera
    this.modelo = null;
  }

  private vaciar(): void {
    for (const m of this.meshes) (m.material as MeshLambertMaterial).dispose();
    if (this.meshes.length) this.grupo.remove(...this.meshes);
    this.meshes = [];
  }

  /** Resalta las elegidas (la principal más fuerte) y engancha el gizmo: a la pieza, o al pivote si son varias. */
  elegir(elegidas: number[], principal: number): void {
    this.elegidas = elegidas.filter((i) => this.meshes[i]);
    this.principal = principal;
    this.arrastre = null;
    this.meshes.forEach((m, i) => (m.material as MeshLambertMaterial).emissive.setHex(i === principal ? 0x3b5f99 : this.elegidas.includes(i) ? 0x1c2f4d : 0));
    this.colocarPivote();
    this.pedir();
  }

  private colocarPivote(): void {
    const ms = this.elegidas.map((i) => this.meshes[i]);
    if (ms.length < 2) {
      if (ms.length) this.transform.attach(ms[0]);
      else this.transform.detach();
      return;
    }
    const caja = new Box3();
    for (const m of ms) caja.expandByObject(m);
    caja.getCenter(this.pivote.position);
    // En local con varias, el pivote toma el giro de la principal.
    if (this.local && this.meshes[this.principal]) this.pivote.quaternion.copy(this.meshes[this.principal].quaternion);
    else this.pivote.quaternion.identity();
    this.pivote.scale.set(1, 1, 1);
    this.pivote.updateMatrixWorld();
    this.transform.attach(this.pivote);
  }

  /** Espacio del gizmo: local (ejes de la pieza) o global. */
  espacio(local: boolean): void {
    this.local = local;
    this.transform.setSpace(local ? "local" : "world");
    this.colocarPivote();
    this.pedir();
  }

  /** Índices de las piezas visibles cuyo centro, proyectado en pantalla, cae dentro de `rect` (coordenadas de cliente). */
  private dentro(rect: DOMRect): number[] {
    const r = this.renderer.domElement.getBoundingClientRect();
    const v = new Vector3();
    this.camara.updateMatrixWorld();
    return this.meshes.flatMap((m, i) => {
      if (!m.visible) return [];
      v.copy(m.position).project(this.camara);
      const x = r.left + ((v.x + 1) / 2) * r.width;
      const y = r.top + ((1 - v.y) / 2) * r.height;
      return v.z < 1 && x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom ? [i] : [];
    });
  }

  private poner(m: Mesh, t: Transformacion): void {
    m.position.set(...t.pos);
    m.rotation.set(t.rot[0], t.rot[1], t.rot[2], "YXZ");
    m.scale.set(...t.esc);
  }

  modo(m: Modo): void {
    this.transform.setMode(m);
    this.pedir();
  }

  ajuste(si: boolean): void {
    this.conAjuste = si;
    this.aplicarAjuste();
  }

  private aplicarAjuste(): void {
    const si = this.conAjuste !== this.invertido;
    this.transform.setTranslationSnap(si ? 0.1 : null);
    this.transform.setRotationSnap(si ? 15 * GRADOS : null);
    this.transform.setScaleSnap(si ? 0.1 : null);
  }

  // Ctrl (o Cmd) pulsado invierte el ajuste a rejilla mientras dure; se mira antes que el gizmo (fase de captura).
  private readonly alPuntero = (e: PointerEvent): void => {
    const inv = e.ctrlKey || e.metaKey;
    if (inv === this.invertido) return;
    this.invertido = inv;
    this.aplicarAjuste();
  };

  /** La transformación de una pieza tal como está ahora en pantalla. */
  transformacion(i: number): Transformacion {
    const m = this.meshes[i];
    return { pos: m.position.toArray(), rot: [m.rotation.x, m.rotation.y, m.rotation.z], esc: m.scale.toArray() };
  }

  /** Un anillo en y=0 por cada círculo de colisión. */
  colision(circulos: Circulo[] | null): void {
    this.anillos.clear();
    for (const [x, z, r] of circulos ?? []) {
      const a = new LineLoop(this.circulo, this.lineaCirculo);
      a.position.set(x, 0, z);
      a.scale.set(r, 1, r);
      this.anillos.add(a);
    }
    this.pedir();
  }

  /** Círculo [x, z, radio] que cubre el asset en x y z, sacado de su caja envolvente. */
  cajaEnvolvente(): Circulo | null {
    const c = new Box3().setFromObject(this.grupo);
    if (c.isEmpty()) return null;
    return [(c.min.x + c.max.x) / 2, (c.min.z + c.max.z) / 2, Math.max(c.max.x - c.min.x, c.max.z - c.min.z) / 2];
  }

  /** Encuadra las piezas indicadas o, si no hay, todo el asset. */
  enfocar(indices: number[]): void {
    const ms = indices.map((i) => this.meshes[i]).filter(Boolean);
    const caja = new Box3();
    if (ms.length) for (const m of ms) caja.expandByObject(m);
    else caja.setFromObject(this.grupo);
    if (caja.isEmpty()) return;
    const centro = caja.getCenter(new Vector3());
    const radio = Math.max(caja.getSize(new Vector3()).length() / 2, 0.25);
    const dir = this.camara.position.clone().sub(this.orbita.target).normalize();
    this.orbita.target.copy(centro);
    this.camara.position.copy(centro).addScaledVector(dir, (radio / Math.sin((this.camara.fov / 2) * GRADOS)) * 1.1);
    this.orbita.update();
    this.pedir();
  }

  // Un clic es pulsar y soltar sin moverse y sin haber cogido el gizmo.
  private readonly alBajar = (e: PointerEvent): void => {
    this.inicio.set(e.clientX, e.clientY);
  };
  private readonly alSubir = (e: PointerEvent): void => {
    if (e.button !== 0 || this.recuadro.reciente || this.inicio.distanceTo(new Vector2(e.clientX, e.clientY)) > 4 || this.transform.axis) return;
    const r = this.renderer.domElement.getBoundingClientRect();
    const rayo = new Raycaster();
    rayo.setFromCamera(new Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), this.camara);
    const golpe = rayo.intersectObjects(
      this.meshes.filter((m) => m.visible),
      false,
    )[0];
    this.alElegir(golpe ? this.meshes.indexOf(golpe.object as Mesh) : -1, e.ctrlKey || e.metaKey);
  };

  dispose(): void {
    this.cerrada = true;
    cancelAnimationFrame(this.pendiente);
    this.observador.disconnect();
    this.recuadro.dispose();
    window.removeEventListener("pointerdown", this.alPuntero, true);
    window.removeEventListener("pointermove", this.alPuntero, true);
    this.renderer.domElement.removeEventListener("pointerdown", this.alBajar);
    this.renderer.domElement.removeEventListener("pointerup", this.alSubir);
    this.transform.dispose();
    this.orbita.dispose();
    this.vaciar();
    this.ponerMalla(undefined);
    this.quitarModelo();
    this.circulo.dispose();
    this.lineaCirculo.dispose();
    this.geometrias.forEach((g) => g.dispose());
    this.escena.traverse((o) => {
      const x = o as Mesh | Line;
      if (!("geometry" in x)) return;
      x.geometry.dispose();
      (Array.isArray(x.material) ? x.material : [x.material]).forEach((m) => m.dispose());
    });
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.renderer.domElement.remove();
  }
}
