// Pinta una escena de canvas (assets/*/escena.html) con Node y Skia, sin navegador.
//
// Las escenas se escribieron para Chromium, pero solo usan canvas 2D. Aquí se
// carga el <script> de la misma escena.html con un `document` mínimo que crea
// lienzos de @napi-rs/canvas (Skia, el mismo motor que pinta el canvas en
// Chrome) y las fuentes de sus @font-face. Lo usa bot/services/node_scene.py.
//
// Uso: node escena_node.cjs ruta/a/escena.html
//
// Protocolo por stdin/stdout, una petición por línea de JSON:
//   {"call": "setup", "args": [meta]}                -> {"ok": true}
//   {"call": "renderFrames", "args": [states, first]} -> {"ok": true, "frames": [[x, y, w, h], ...]}
//                                                       y detrás los píxeles RGBA de cada recuadro
//   {"call": "<expr>", "eval": true}                  -> {"ok": true, "value": ...}
// Si algo falla: {"ok": false, "error": "..."}. Al arrancar escribe {"ready": true}.
//
// Los fotogramas salen en RGBA crudo y no en PNG: comprimir PNG en un solo hilo
// era lo más caro de dibujar en Chromium, y Python lo descomprimía otra vez.

"use strict";
const fs = require("fs");
const path = require("path");
const readline = require("readline");
const vm = require("vm");
const { createCanvas, GlobalFonts } = require("@napi-rs/canvas");

const scenePath = path.resolve(process.argv[2]);
const html = fs.readFileSync(scenePath, "utf8");

for (const [, family, url] of html.matchAll(/@font-face\s*{[^}]*font-family:\s*"([^"]+)"[^}]*url\("([^"]+)"\)/g)) {
  GlobalFonts.registerFromPath(path.resolve(path.dirname(scenePath), url), family);
}

globalThis.window = globalThis;
globalThis.document = {
  createElement(tag) {
    if (tag !== "canvas") throw new Error(`La escena pide un <${tag}>: sin navegador solo hay canvas`);
    return createCanvas(300, 150);
  },
  // Las fuentes ya están registradas antes de ejecutar la escena.
  fonts: { load: async () => [], ready: Promise.resolve(), check: () => true },
};
// La escena devuelve cada recuadro con exportFrame(lienzo); en Chromium es un
// data URL de PNG, aquí los píxeles tal cual.
globalThis.exportFrame = (canvas) => ({
  w: canvas.width,
  h: canvas.height,
  rgba: canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data,
});

const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
vm.runInThisContext(scripts.join("\n"), { filename: scenePath });

function send(header, buffers = []) {
  process.stdout.write(JSON.stringify(header) + "\n");
  for (const buffer of buffers) process.stdout.write(buffer);
}

async function handle(request) {
  if (request.eval) return send({ ok: true, value: await vm.runInThisContext(request.call) });
  const result = await globalThis[request.call](...(request.args || []));
  if (request.call !== "renderFrames") return send({ ok: true });
  const frames = result.map((p) => [p.x, p.y, p.u.w, p.u.h]);
  const buffers = result.map((p) => Buffer.from(p.u.rgba.buffer, p.u.rgba.byteOffset, p.u.rgba.byteLength));
  send({ ok: true, frames }, buffers);
}

// Las peticiones se atienden de una en una y en orden: la escena guarda estado
// entre fotogramas (el recuadro anterior, el marcador).
let queue = Promise.resolve();
readline.createInterface({ input: process.stdin }).on("line", (line) => {
  queue = queue.then(() => handle(JSON.parse(line))).catch((error) => send({ ok: false, error: String(error && error.stack || error) }));
});
send({ ready: true });
