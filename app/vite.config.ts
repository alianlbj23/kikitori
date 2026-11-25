import { defineConfig } from "vite";
// @ts-expect-error node types are not installed; this file only runs in Node.
import process from "node:process";

const host = process.env.TAURI_DEV_HOST;

/** `src/locales/<locale>.ts` → chunk `locale-<locale>`; everything else keeps the default splitting. */
function localeChunk(moduleId: string): string | null {
  const m = /[\\/]src[\\/]locales[\\/]([A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?)\.ts$/.exec(moduleId);
  return m ? `locale-${m[1]}` : null;
}

// https://v2.tauri.app/start/frontend/vite/
export default defineConfig(() => ({
  // Keep Rust compiler output visible in `tauri dev`.
  clearScreen: false,
  build: {
    // The app loads its chunks from local disk inside the WebView, never over
    // the network, so the default 500 kB web warning does not apply.
    chunkSizeWarningLimit: 1024,
    rolldownOptions: {
      // Two windows, two pages.
      input: { main: "index.html", overlay: "overlay.html" },
      output: {
        // One chunk per UI dictionary (13 of them, ~60-120 kB each) instead of
        // one shared chunk that outgrew the limit above. They are still
        // imported statically, so switching the interface language stays
        // synchronous; lazy-loading per locale would gain nothing measurable.
        codeSplitting: { groups: [{ name: localeChunk, debugName: "locales" }] },
      },
    },
  },
  server: {
    // Tauri expects a fixed port (devUrl in tauri.conf.json).
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host ? { protocol: "ws", host, port: 1421 } : undefined,
    watch: { ignored: ["**/src-tauri/**"] },
  },
  test: {
    include: ["src/**/*.test.ts"],
    environment: "node",
    // CSS is processed only so `main.css?raw` (render-cost.test.ts) has its text.
    css: { include: [/\/main\.css(\?|$)/] },
  },
}));
