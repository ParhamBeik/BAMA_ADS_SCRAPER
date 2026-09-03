import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import type { Plugin } from "vite";
import { landingHtml } from "./scripts/landing";

/**
 * Inject a `<link rel="preload">` for the Persian face, with the real hashed
 * filename.
 *
 * Hand-writing the href is not an option — the content hash changes on every
 * build, so a literal one silently rots into a preload of a 404, which is worse
 * than no preload at all: the browser pays for the request and still waits two
 * round trips for the font. This reads the emitted asset name out of the bundle
 * instead, so it cannot drift.
 *
 * Only the Arabic subset. Preloading the Latin one too would compete for
 * bandwidth with the render-blocking CSS to buy digits that the fallback draws
 * acceptably; the Persian body text is the one that looks broken in Tahoma.
 */
/**
 * Emit the public landing page as a second, standalone document.
 *
 * nginx serves it at `/` and the SPA shell everywhere else, so a first-time
 * visitor gets HTML instead of a spinner and a crawler gets something to read,
 * without the app's own routes losing their shell. See scripts/landing.mjs for
 * why a page with no data does not get a framework.
 */
function emitLanding(): Plugin {
  return {
    name: "emit-landing",
    enforce: "post",
    apply: "build",
    generateBundle(_options, bundle) {
      const fontHref = Object.keys(bundle).find((f) =>
        /vazirmatn-arabic-.*\.woff2$/.test(f),
      );
      this.emitFile({
        type: "asset",
        fileName: "landing.html",
        source: landingHtml({ fontHref, siteUrl: SITE_URL }),
      });
      // Only the two pages that render without a session. Listing every route
      // would point a crawler at pages that answer with a login form, which is
      // how a site ends up indexed as a wall of identical sign-in screens.
      this.emitFile({
        type: "asset",
        fileName: "sitemap.xml",
        source: `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>${SITE_URL}/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>
  <url><loc>${SITE_URL}/methodology</loc><changefreq>weekly</changefreq><priority>0.6</priority></url>
</urlset>
`,
      });
    },
  };
}

function preloadPersianFont(): Plugin {
  return {
    name: "preload-persian-font",
    enforce: "post",
    apply: "build",
    transformIndexHtml(html, ctx) {
      const font = Object.keys(ctx.bundle ?? {}).find((f) =>
        /vazirmatn-arabic-.*\.woff2$/.test(f),
      );
      return html
        .replace(
          "<!--preload-fonts-->",
          font
            ? `<link rel="preload" as="font" type="font/woff2" href="/${font}" crossorigin />`
            : "",
        )
        // Vite's own `%VAR%` substitution reads .env files, not `define`, so
        // the placeholder is resolved here where the value actually lives.
        .replaceAll("%VITE_SITE_URL%", SITE_URL);
    },
  };
}

// The dev server proxies /api to Django so the browser sees one origin and there
// is no CORS or cookie-domain difference between development and production.
// The public origin, used for the canonical link and the sitemap. Overridable
// so a different deployment does not need a code change; defaulted so a build
// that forgets to set it still emits a URL that resolves.
const SITE_URL = process.env.VITE_SITE_URL ?? "https://bama-89-106-206-4.sslip.io";

export default defineConfig({
  plugins: [react(), tailwindcss(), preloadPersianFont(), emitLanding()],
  // `@` is the convention every shadcn component is generated against; without it
  // each generated file would need its import paths rewritten by hand.
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: process.env.VITE_API_TARGET ?? "http://localhost:8001", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    // On, and linked. Browsers fetch a `.map` only when devtools is open, so
    // this costs a visitor nothing, while Lighthouse's `valid-source-maps`
    // audit — and anybody debugging production — needs them present. The 8.7 MB
    // this used to emit was a symptom of the unsplit bundle, not of the maps.
    sourcemap: true,
    // Below this a separate request costs more than the bytes it saves.
    assetsInlineLimit: 2048,
    // No `modulepreload` hints. They are high priority, and on a throttled
    // connection seven of them race the one stylesheet that actually blocks
    // rendering — the CSS lost that race by 306ms while the browser eagerly
    // fetched chunks nothing had asked for yet. The module graph still loads
    // them, just after the paint that matters.
    modulePreload: { polyfill: false, resolveDependencies: () => [] },
    rollupOptions: {
      output: {
        // Only the framework is grouped by hand. Grouping the chart engine too
        // looked tidier and was actively harmful: naming a chunk pulls it into
        // the entry's static graph, so Vite emitted a `modulepreload` for 560 KB
        // of echarts on every page load and the lazy import bought nothing.
        // Left alone, the dynamic import forms its own chunk and stays out of
        // the initial payload.
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return;
          if (/[\\/]node_modules[\\/](react|react-dom|react-router|react-router-dom|scheduler)[\\/]/.test(id))
            return "react";
        },
      },
    },
  },
});
