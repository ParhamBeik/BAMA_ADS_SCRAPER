/**
 * Everything a crawler reads, generated from one origin.
 *
 * The sitemap and robots.txt both name the site's own host, and they have to
 * name the *same* one: a robots.txt advertising a sitemap on a different host is
 * discarded by Google as a cross-host sitemap. That is why `siteUrl` is threaded
 * through both from a single definition in vite.config.ts rather than each file
 * carrying its own literal — the first version of this got one file right and
 * left the other behind.
 *
 * They are emitted by the build rather than kept in `public/` for the same
 * reason. A file in `public/` is copied verbatim, so it cannot pick up the
 * origin, and an emitted asset of the same name silently wins — leaving a
 * committed file that looks authoritative, is served by `npm run dev`, and
 * never reaches production.
 *
 * This module used to emit a static landing page at `/` as well. It is gone:
 * `/` is the application, and an anonymous visitor there is sent to the sign-in
 * form. The cost of that is real and worth stating — the landing page was the
 * only document a crawler could read, and without it the sitemap below has one
 * entry.
 */

/**
 * The only route that renders a page, rather than a sign-in form, without a
 * session.
 *
 * `/` is deliberately absent. It now answers with the app shell, which redirects
 * a signed-out reader to `/login`, and pointing a crawler at a login redirect is
 * how a site ends up indexed as a wall of identical sign-in screens.
 */
export function sitemapXml(siteUrl: string): string {
  return `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>${siteUrl}/methodology</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>
</urlset>
`;
}

/**
 * The crawl policy, with the sitemap on the same host as everything else.
 *
 * The `Allow`/`Disallow` split mirrors `AuthRoutes` in App.tsx: the methodology
 * page renders for a signed-out reader, and every other path — `/` included —
 * redirects to a login form that is not worth indexing.
 */
export function robotsTxt(siteUrl: string): string {
  return `User-agent: *
Allow: /methodology
# The only route that renders without a session; see App.tsx AuthRoutes.
# Everything else needs one, so crawling it only produces login pages.
Disallow: /$
Disallow: /deals
Disallow: /explore
Disallow: /analyse
Disallow: /listing
Disallow: /saved
Disallow: /alerts
Disallow: /budget
Disallow: /control
Disallow: /api/

Sitemap: ${siteUrl}/sitemap.xml
`;
}
