// Configuración de Vite para la página del Jovaniverso: compila en src/bot/assets/universo
// (lo que sirve el bot) y, en desarrollo, reenvía /api al servidor del bot.
import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: "../src/bot/assets/universo",
    emptyOutDir: true,
  },
  server: {
    proxy: { "/api": "http://localhost:8080" },
  },
});
