/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite config for the Budapest Transit Monitor frontend.
//
// - The dev server proxies /api to the FastAPI backend (default :8000) so the
//   browser can call the API without CORS friction during development.
// - Vitest is configured with jsdom + a global setup file for React Testing
//   Library (used by component tests added in later tasks).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target:
          process.env["VITE_BACKEND_ORIGIN"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
