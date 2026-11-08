import { defineConfig } from "vite";
// @ts-expect-error node types are not installed; this file only runs in Node.
import process from "node:process";

const host = process.env.TAURI_DEV_HOST;

// https://v2.tauri.app/start/frontend/vite/
export default defineConfig(() => ({
  // Keep Rust compiler output visible in `tauri dev`.
  clearScreen: false,
  build: {
    // The 13 UI dictionaries share one ~640 kB chunk (~170 kB gzip). The app
    // loads it from local disk inside the WebView, never over the network, so
    // the default 500 kB web warning does not apply; lazy-loading per locale
    // would make every locale switch asynchronous for no measurable gain.
    chunkSizeWarningLimit: 1024,
    rollupOptions: {
      // Two windows, two pages.
      input: { main: "index.html", overlay: "overlay.html" },
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
