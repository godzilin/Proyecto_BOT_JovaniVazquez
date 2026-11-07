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
  type Object3D,
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
import { type Forma, geometriaForma, type Pieza, type Vec3 } from "./formato";

export type Modo = "translate" | "rotate" | "scale";

const GRADOS = Math.PI / 180;

export class Vista {
  readonly renderer = new WebGLRenderer({ antialias: true });
  /** Se llama en cada paso del arrastre de una pieza y, con `fin`, al soltarla. */
  alTransformar: (i: number, fin: boolean) => void = () => {};
  /** Clic sobre una pieza (o sobre nada: -1). */
  alElegir: (i: number) => void = () => {};

  private readonly escena = new Scene();
  private readonly camara = new PerspectiveCamera(50, 1, 0.05, 500);
  private readonly orbita: OrbitControls;
  private readonly transform: TransformControls;
  private readonly grupo = new Group();
  private readonly anillo: LineLoop;
  private readonly geometrias = new Map<Forma, BufferGeometry>();
  private meshes: Mesh[] = [];
  private modelo: Object3D | null = null;
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
    this.anillo = new LineLoop(new BufferGeometry().setFromPoints(puntos), new LineBasicMaterial({ color: 0xffd86b }));
    this.anillo.visible = false;
    this.escena.add(this.anillo);

    this.camara.position.set(3, 2.5, 4);
    this.orbita = new OrbitControls(this.camara, r.domElement);
    this.orbita.target.set(0, 0.5, 0);
    this.orbita.addEventListener("change", () => this.pedir());

    this.transform = new TransformControls(this.camara, r.domElement);
    this.transform.addEventListener("change", () => this.pedir());
    this.transform.addEventListener("dragging-changed", (e) => {
      this.orbita.enabled = !e.value;
      if (!e.value) this.alTransformar(this.meshes.indexOf(this.transform.object as Mesh), true);
    });
    this.transform.addEventListener("objectChange", () => this.alTransformar(this.meshes.indexOf(this.transform.object as Mesh), false));
    this.escena.add(this.transform.getHelper());

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

  private geometria(f: Forma): BufferGeometry {
    let g = this.geometrias.get(f);
    if (!g) this.geometrias.set(f, (g = geometriaForma(f)));
    return g;
  }

  /** Pone las piezas en la escena: si hay las mismas que antes se actualizan, si no se rehacen. */
  mostrarPiezas(piezas: Pieza[], elegida: number): void {
    this.quitarModelo();
    if (this.meshes.length !== piezas.length) {
      this.transform.detach();
      this.vaciar();
      this.meshes = piezas.map(() => new Mesh(undefined, new MeshLambertMaterial({ flatShading: true })));
      this.grupo.add(...this.meshes);
    }
    piezas.forEach((p, i) => {
      const m = this.meshes[i];
      m.geometry = this.geometria(p.forma);
      (m.material as MeshLambertMaterial).color.setHex(p.color);
      m.position.set(...p.pos);
      m.rotation.set(p.rot[0], p.rot[1], p.rot[2], "YXZ");
      m.scale.set(...p.esc);
    });
    this.elegir(elegida);
  }

  mostrarModelo(modelo: Object3D): void {
    this.transform.detach();
    this.vaciar();
    this.quitarModelo();
    this.modelo = modelo.clone(true);
    this.grupo.add(this.modelo);
    this.pedir();
  }

  /** Deja la escena sin asset. */
  limpiar(): void {
    this.transform.detach();
    this.vaciar();
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

  elegir(i: number): void {
    if (i >= 0 && this.meshes[i]) this.transform.attach(this.meshes[i]);
    else this.transform.detach();
    this.pedir();
  }

  modo(m: Modo): void {
    this.transform.setMode(m);
    this.pedir();
  }

  ajuste(si: boolean): void {
    this.transform.setTranslationSnap(si ? 0.1 : null);
    this.transform.setRotationSnap(si ? 15 * GRADOS : null);
    this.transform.setScaleSnap(si ? 0.1 : null);
  }

  /** La transformación de una pieza tal como está ahora en pantalla. */
  transformacion(i: number): { pos: Vec3; rot: Vec3; esc: Vec3 } {
    const m = this.meshes[i];
    return { pos: m.position.toArray(), rot: [m.rotation.x, m.rotation.y, m.rotation.z], esc: m.scale.toArray() };
  }

  colision(radio: number | null): void {
    this.anillo.visible = radio !== null;
    if (radio !== null) this.anillo.scale.set(radio, 1, radio);
    this.pedir();
  }

  /** Radio que cubre el asset en x y z, centrado en el origen. */
  radioEnvolvente(): number {
    const c = new Box3().setFromObject(this.grupo);
    return c.isEmpty() ? 0 : Math.max(Math.abs(c.min.x), Math.abs(c.max.x), Math.abs(c.min.z), Math.abs(c.max.z));
  }

  /** Encuadra la pieza elegida o, si no hay, todo el asset. */
  enfocar(i: number): void {
    const caja = new Box3().setFromObject(this.meshes[i] ?? this.grupo);
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
    if (e.button !== 0 || this.inicio.distanceTo(new Vector2(e.clientX, e.clientY)) > 4 || this.transform.axis) return;
    const r = this.renderer.domElement.getBoundingClientRect();
    const rayo = new Raycaster();
    rayo.setFromCamera(new Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), this.camara);
    const golpe = rayo.intersectObjects(this.meshes, false)[0];
    this.alElegir(golpe ? this.meshes.indexOf(golpe.object as Mesh) : -1);
  };

  dispose(): void {
    this.cerrada = true;
    cancelAnimationFrame(this.pendiente);
    this.observador.disconnect();
    this.renderer.domElement.removeEventListener("pointerdown", this.alBajar);
    this.renderer.domElement.removeEventListener("pointerup", this.alSubir);
    this.transform.dispose();
    this.orbita.dispose();
    this.vaciar();
    this.quitarModelo();
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
