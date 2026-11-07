// Configuración de Vite para la página del Jovaniverso: compila en src/bot/assets/universo
// (lo que sirve el bot) y, en desarrollo, reenvía /api al servidor del bot.
// También copia en assets/tragas/ la escena de la tragaperras clásica del bot (la misma que pinta
// los GIF de Discord, con sus símbolos y fuentes): las máquinas de la plaza la pintan en directo.
import { readFileSync } from "node:fs";
import { extname, resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";

// Sin @types/node a propósito: tsconfig no incluye este archivo (lo transpila Vite al arrancar).
const BOT_ASSETS = resolve(import.meta.dirname, "../src/bot/assets");
/** Lo que necesita `slots/escena.html`, con las rutas relativas que usa (`../tragas.js`, `../botes/fonts/…`). */
const TRAGAS = [
  "tragas.js",
  "slots/escena.html",
  "slots/bell.png",
  "slots/cherry.png",
  "slots/diamond.png",
  "slots/grape.png",
  "slots/lemon.png",
  "slots/scatter.png",
  "slots/seven.png",
  "slots/wild.png",
  "botes/fonts/luckiest-guy.woff",
  "memes/fonts/MontserratBold.ttf",
];
const TIPOS: Record<string, string> = {
  ".js": "text/javascript",
  ".html": "text/html",
  ".png": "image/png",
  ".woff": "font/woff",
  ".ttf": "font/ttf",
};

function escenaTragas(): Plugin {
  return {
    name: "escena-tragas",
    configureServer(server) {
      server.middlewares.use("/assets/tragas/", (req, res, next) => {
        const ruta = (req.url ?? "").split("?")[0].replace(/^\//, "");
        if (!TRAGAS.includes(ruta)) return next();
        res.setHeader("Content-Type", TIPOS[extname(ruta)] ?? "application/octet-stream");
        res.end(readFileSync(resolve(BOT_ASSETS, ruta)));
      });
    },
    generateBundle() {
      for (const ruta of TRAGAS) {
        this.emitFile({ type: "asset", fileName: `assets/tragas/${ruta}`, source: readFileSync(resolve(BOT_ASSETS, ruta)) });
      }
    },
  };
}

export default defineConfig({
  plugins: [escenaTragas()],
  build: {
    outDir: "../src/bot/assets/universo",
    emptyOutDir: true,
    chunkSizeWarningLimit: 900, // three.js solo pesa ~700 kB; no hay nada que partir
  },
  server: {
    proxy: { "/api": { target: "http://localhost:8080", ws: true } },
  },
});
