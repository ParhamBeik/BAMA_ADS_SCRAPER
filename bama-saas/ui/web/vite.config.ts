import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The dev server proxies /api to Django so the browser sees one origin and there
// is no CORS or cookie-domain difference between development and production.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // `@` is the convention every shadcn component is generated against; without it
  // each generated file would need its import paths rewritten by hand.
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: process.env.VITE_API_TARGET ?? "http://localhost:8001", changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
