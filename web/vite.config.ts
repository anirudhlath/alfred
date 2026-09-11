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
  },
});
