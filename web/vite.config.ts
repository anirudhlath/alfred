/// <reference types="vitest/config" />
import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    proxy: {
      "/api": "http://localhost:8081",
      "/health": "http://localhost:8081",
      "/ws": { target: "ws://localhost:8081", ws: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    // On, so `import css from "…/index.css?raw"` hands a test the real file
    // rather than the empty string vitest stubs every `.css` import with:
    // `src/test/contrast.test.ts` holds the restated design tokens to the
    // stylesheet, and a guard that reads "" would pass on anything. The
    // narrower `css: { include: [...] }` form does not reach `?raw`.
    css: true,
    // The three cleanups every file currently writes by hand. Set here so a
    // file added later cannot forget one: a spy left on `console.warn`, a
    // `fetch` left stubbed or an env left overridden is state the *next* file
    // inherits, and the failure it causes lands nowhere near the file that
    // caused it. Every existing `vi.restoreAllMocks()` / `vi.unstubAllGlobals()`
    // stays where it is — they are now belt and braces rather than the belt.
    restoreMocks: true,
    unstubGlobals: true,
    unstubEnvs: true,
  },
});
