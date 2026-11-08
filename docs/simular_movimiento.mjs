// Simula el movimiento del Jovaniverso (`universo/src/mundo/jugador.ts`) en el extraplano de pruebas, sin navegador:
// carga el código de verdad con Vite (SSR) y comprueba el wallrun, el salto de pared, la cadena por el pasillo, el doble
// salto, el bunny hop, el gancho a la torre, rodear una columna y el tope de velocidad. Para retocar los números del
// movimiento sin abrir Discord: `node docs/simular_movimiento.mjs` (con `npm install` hecho en `universo/`). Sale con
// error si algo falla.
import { fileURLToPath, pathToFileURL } from "node:url";

globalThis.location = { search: "?mundo=pruebas" };
const raiz = fileURLToPath(new URL("../universo", import.meta.url));
const { createServer } = await import(pathToFileURL(`${raiz}/node_modules/vite/dist/node/index.js`).href);
const vite = await createServer({ root: raiz, logLevel: "error", server: { middlewareMode: true }, appType: "custom" });
const three = await vite.ssrLoadModule("three");
const { Jugador } = await vite.ssrLoadModule("/src/mundo/jugador.ts");
const { añadirCirculo } = await vite.ssrLoadModule("/src/mundo/colision.ts");

const modelo = new three.Group();
modelo.add(new three.Mesh(new three.BoxGeometry(1, 2, 1)));
const clips = ["Idle", "Walking", "Running", "Jump", "Agachado", "Deslizar"].map((n) => new three.AnimationClip(n, 1, []));
const nuevo = () => new Jugador(modelo.clone(), clips);

const DT = 1 / 60;
function correr(j, segundos, entrada, cada) {
  for (let i = 0; i < Math.round(segundos / DT); i++) {
    const e = typeof entrada === "function" ? entrada(i * DT, j) : entrada;
    j.actualizar(DT, e);
    cada?.(i * DT, j);
  }
}
const vel = (j) => Math.hypot(j.vx, j.vz).toFixed(1);
let fallos = 0;
const ok = (cond, msg) => {
  if (!cond) fallos++;
  console.log(`${cond ? "OK " : "MAL"} ${msg}`);
};

// 1. Wallrun: corriendo hacia -z por el pasillo (x 30.6..35.4), pegado a la pared de x=35.4, saltando.
{
  const j = nuevo();
  j.teletransportar(34.4, 82);
  const base = { dx: 0.15, dz: -1, fuerza: 1, correr: true, saltar: false, agachar: false };
  correr(j, 0.6, base);
  let enPared = 0, alto = 0, cayo = false, vmax = 0;
  correr(j, 2.4, (t) => ({ ...base, saltar: t < DT }), (t, j) => {
    if (j.pared) enPared += DT;
    alto = Math.max(alto, j.y);
    vmax = Math.max(vmax, Math.hypot(j.vx, j.vz));
  });
  ok(enPared > 1, `wallrun: ${enPared.toFixed(2)} s en la pared, altura máx ${alto.toFixed(2)} m, vel máx ${vmax.toFixed(1)} m/s, wallruns=${j.tecnica.wallruns}`);
}

// 2. Salto de pared y cadena por el pasillo: saltar cada 0,45 s en la pared, empujando hacia la otra.
{
  const j = nuevo();
  j.teletransportar(34.4, 82);
  let lado = 1;
  const e = { dx: 0.15, dz: -1, fuerza: 1, correr: true, saltar: false, agachar: false };
  correr(j, 0.6, e);
  let tPared = 0, saltar = true;
  correr(j, 5, (t, j) => {
    let s = saltar;
    saltar = false;
    if (j.pared) {
      tPared += DT;
      if (tPared > 0.35) {
        s = true;
        tPared = 0;
        lado = j.pared.x > 0 ? 1 : -1; // salir hacia la otra pared
      }
    } else tPared = 0;
    const dx = 0.35 * lado;
    const l = Math.hypot(dx, 1);
    return { ...e, dx: dx / l, dz: -1 / l, saltar: s };
  });
  ok(j.tecnica.walljumps >= 3 && j.tecnica.chain >= 3, `cadena: ${j.tecnica.walljumps} saltos de pared, cadena ${j.tecnica.chain}, vel ${vel(j)} m/s, z=${j.z.toFixed(1)}`);
}

// 3. Doble salto: sube más que uno solo y se recarga al tocar el suelo.
{
  const j = nuevo();
  j.teletransportar(-4, 66);
  const quieto = { dx: 0, dz: 0, fuerza: 0, correr: false, saltar: false, agachar: false };
  let alto = 0;
  correr(j, 2, (t) => ({ ...quieto, saltar: t < DT || Math.abs(t - 0.45) < DT / 2 }), (t, j) => (alto = Math.max(alto, j.y)));
  ok(alto > 2 && j.tecnica.doublejumps === 1, `doble salto: altura ${alto.toFixed(2)} m (uno solo, 1,3), dobles=${j.tecnica.doublejumps}`);
  correr(j, 2, (t) => ({ ...quieto, saltar: t < DT || Math.abs(t - 0.45) < DT / 2 }));
  ok(j.tecnica.doublejumps === 2, "el doble salto se recarga en el suelo");
}

// 4. Bunny hop: pulsar justo antes de caer no gasta el doble salto.
{
  const j = nuevo();
  j.teletransportar(-4, 66);
  const quieto = { dx: 0, dz: 0, fuerza: 0, correr: false, saltar: false, agachar: false };
  let primeroAbajo = -1;
  correr(j, 1.5, (t, j) => {
    const cerca = j.vy < 0 && j.y < 0.4 && !j.suelo && primeroAbajo < 0;
    if (cerca) primeroAbajo = t;
    return { ...quieto, saltar: t < DT || cerca };
  });
  ok(j.tecnica.doublejumps === 0 && j.saltos === 2, `bhop: saltos=${j.saltos}, dobles=${j.tecnica.doublejumps}`);
}

// 5. Gancho: a lo alto de la torre (46..50 x 60..64, 10 m) desde (40, 62), mirando hacia +x y algo arriba.
{
  const j = nuevo();
  j.teletransportar(40, 62);
  const d = new three.Vector3(1, 1.3, 0).normalize();
  const ok1 = j.lanzarGancho(d.x, d.y, d.z);
  let alto = 0, vmax = 0;
  correr(j, 2.2, { dx: 1, dz: 0, fuerza: 1, correr: false, saltar: false, agachar: false }, (t, j) => {
    alto = Math.max(alto, j.y);
    vmax = Math.max(vmax, Math.hypot(j.vx, j.vz));
  });
  ok(ok1 && j.y > 9.9 && j.suelo, `gancho (acaba encima de la torre, y=${j.y.toFixed(1)}): enganchado=${ok1}, ancla=${j.gancho ? "sigue" : "suelto"}, altura máx ${alto.toFixed(1)} m, vel máx ${vmax.toFixed(1)}, recarga ${j.recargaGancho.toFixed(1)} s, ganchos=${j.tecnica.grapples}`);
  ok(!j.lanzarGancho(d.x, d.y, d.z) || j.recargaGancho === 0, "el gancho no se relanza en recarga");
  const j2 = nuevo();
  j2.teletransportar(40, 62);
  ok(!j2.lanzarGancho(0, 1, 0), "al cielo no se engancha");
}

// 6. Columna gorda (un círculo de 2 m, lejos de la pista): rodearla corriendo por ella.
{
  añadirCirculo(100, 100, 2);
  const j = nuevo();
  j.teletransportar(102.6, 94);
  const e = { dx: 0, dz: 1, fuerza: 1, correr: true, saltar: false, agachar: false };
  correr(j, 0.5, e);
  let enPared = 0;
  // Como quien gira la cámara al rodearla: empuja hacia donde va.
  correr(j, 2, (t, j) => {
    const r = Math.hypot(j.vx, j.vz) || 1;
    return { ...e, dx: j.vx / r, dz: j.vz / r, saltar: t < DT };
  }, (t, j) => j.pared && (enPared += DT));
  ok(enPared > 0.5, `columna: ${enPared.toFixed(2)} s rodeándola, en (${j.x.toFixed(1)}, ${j.z.toFixed(1)})`);
}

// 7. Tope de velocidad.
{
  const j = nuevo();
  j.teletransportar(-4, 66);
  j.vx = 50;
  correr(j, 0.05, { dx: 1, dz: 0, fuerza: 1, correr: true, saltar: false, agachar: false });
  ok(Math.hypot(j.vx, j.vz) <= 20.001, `tope: ${vel(j)} m/s`);
}
await vite.close();
process.exitCode = fallos ? 1 : 0;
