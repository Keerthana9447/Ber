import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The dev server proxies /api to the FastAPI backend, so the browser sees one origin
// and no CORS configuration is needed in development.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const backend = env.VITE_BACKEND_URL || "http://127.0.0.1:8000";
  return {
    plugins: [react()],
    server: { port: 5173, proxy: { "/api": { target: backend, changeOrigin: false } } },
    build: {
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (id.includes("/node_modules/victory-vendor/")) return "chart-vendor";
            if (id.includes("/node_modules/recharts/")) return "charts";
          },
        },
      },
    },
    test: { environment: "jsdom", globals: true },
  };
});
