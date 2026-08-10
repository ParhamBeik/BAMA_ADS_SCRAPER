import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api to Django so the browser sees one origin and there
// is no CORS or cookie-domain difference between development and production.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: process.env.VITE_API_TARGET ?? "http://localhost:8001", changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
