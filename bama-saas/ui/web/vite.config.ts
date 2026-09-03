import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import type { Plugin } from "vite";
import { landingHtml, robotsTxt, sitemapXml } from "./scripts/landing";

// The public origin: the canonical link, the sitemap's `<loc>`s, and the
// `Sitemap:` line in robots.txt all come from here, because a crawler discards
// a sitemap advertised from a different host than the one it is on.
//
// `||`, not `??`: Docker's `ENV VITE_SITE_URL=$VITE_SITE_URL` sets the variable
// to the empty string when the build arg is not passed, and an empty string is
// not nullish — `??` would let a build with no arg emit `<loc></loc>`.
//
// Trailing slashes are stripped because every use site appends its own path.
// `https://host/` produced `href="https://host//"`, a canonical naming a URL
// the site does not serve, which is the exact failure a canonical is meant to
// prevent.
const SITE_URL = (process.env.VITE_SITE_URL || "https://bama-89-106-206-4.sslip.io")
  .replace(/\/+$/, "");

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
      // Emitted, not kept in `public/`: both of these name the site's origin,
      // and a file copied verbatim out of `public/` cannot pick it up. They
      // used to exist in both places, where the emitted copy silently won and
      // the committed one was what everybody edited.
      this.emitFile({ type: "asset", fileName: "sitemap.xml", source: sitemapXml(SITE_URL) });
      this.emitFile({ type: "asset", fileName: "robots.txt", source: robotsTxt(SITE_URL) });
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
      return html.replace(
        "<!--preload-fonts-->",
        font
          ? `<link rel="preload" as="font" type="font/woff2" href="/${font}" crossorigin />`
          : "",
      );
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), preloadPersianFont(), emitLanding()],
  // `@` is the convention every shadcn component is generated against; without it
  // each generated file would need its import paths rewritten by hand.
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  // The dev server proxies /api to Django so the browser sees one origin and
  // there is no CORS or cookie-domain difference between development and
  // production.
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
