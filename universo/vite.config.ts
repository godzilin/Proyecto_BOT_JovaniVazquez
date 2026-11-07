// Configuración de Vite para la página del Jovaniverso: compila en src/bot/assets/universo
// (lo que sirve el bot) y, en desarrollo, reenvía /api al servidor del bot.
// Las tragaperras de la plaza importan la escena de la clásica del bot (src/bot/assets: tragas.js,
// slots/escena.js, sus símbolos y fuentes; ver src/casino/escena.ts), fuera de esta carpeta.
import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: "../src/bot/assets/universo",
    emptyOutDir: true,
    chunkSizeWarningLimit: 900, // three.js solo pesa ~700 kB; no hay nada que partir
  },
  server: {
    proxy: { "/api": { target: "http://localhost:8080", ws: true } },
    fs: { allow: [".", "../src/bot/assets"] },
  },
});
