/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The browser only talks to this dev server; it forwards API paths to the local FastAPI backend,
// so the frontend uses relative URLs and needs no CORS. Both servers stay bound to 127.0.0.1.
const BACKEND = "http://127.0.0.1:8000";
const proxy = {
  "/chat": { target: BACKEND, changeOrigin: false },
  "/export": { target: BACKEND, changeOrigin: false },
  "/health": { target: BACKEND, changeOrigin: false },
  "/catalog": { target: BACKEND, changeOrigin: false },
  "/admin": { target: BACKEND, changeOrigin: false },
};

export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", port: 5173, strictPort: true, proxy },
  preview: { host: "127.0.0.1", port: 4173, strictPort: true, proxy },
  // Plotly is loaded on demand in its own chunk; it is large by nature.
  build: { chunkSizeWarningLimit: 1500 },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
