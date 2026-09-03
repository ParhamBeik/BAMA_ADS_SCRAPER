/**
 * Everything a crawler reads, generated from one origin.
 *
 * The landing page, the sitemap and robots.txt all name the site's own host,
 * and they have to name the *same* one: a robots.txt advertising a sitemap on a
 * different host is discarded by Google as a cross-host sitemap, and a canonical
 * pointing at a host that does not resolve tells a crawler to index nothing.
 * That is why `siteUrl` is threaded through all three from a single definition
 * in vite.config.ts rather than each file carrying its own literal — the first
 * two versions of this each got one file right and left another behind.
 *
 * They are emitted by the build rather than kept in `public/` for the same
 * reason. A file in `public/` is copied verbatim, so it cannot pick up the
 * origin, and an emitted asset of the same name silently wins — leaving a
 * committed file that looks authoritative, is served by `npm run dev`, and
 * never reaches production.
 */

/**
 * The public entry page, emitted as a complete standalone document.
 *
 * Not a React route, and that is the whole point. Everything else in this app
 * is behind a session, so the first thing a new visitor met was a login form
 * that could not paint until the bundle had downloaded, parsed, executed and
 * checked the session — 3.4s to first paint under mobile throttling, for a
 * screen with eleven words on it.
 *
 * A page with no data and no interaction does not need a framework. This one
 * ships as HTML with its critical CSS inline, so first paint is one round trip
 * and the largest element is in the markup rather than painted after boot. It
 * is also the only page a crawler can read, which is what makes the product
 * indexable at all.
 *
 * The font href is passed in rather than hardcoded: it carries a content hash
 * that changes on every build, and a literal one rots into a preload of a 404.
 * The site URL is passed in for a duller reason — the first version of this
 * hardcoded a hostname that did not resolve, which is worse than shipping no
 * canonical at all, because a canonical pointing somewhere dead tells a crawler
 * to index nothing.
 */
export function landingHtml(
  { fontHref, siteUrl }: { fontHref?: string; siteUrl: string },
): string {
  const preload = fontHref
    ? `<link rel="preload" as="font" type="font/woff2" href="/${fontHref}" crossorigin />`
    : "";
  const fontFace = fontHref
    ? `@font-face{font-family:"Vazirmatn Variable";font-style:normal;font-display:swap;
       font-weight:100 900;src:url("/${fontHref}") format("woff2-variations");
       unicode-range:U+0600-06FF,U+0750-077F,U+200C-200E,U+FB50-FDFF,U+FE70-FEFC;}`
    : "";
  return `<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
<title>بازار خودرو باما — قیمت منصفانه‌ی خودرو بر پایه‌ی آگهی‌های واقعی</title>
<meta name="description" content="قیمت منصفانه‌ی هر خودرو بر پایه‌ی آگهی‌های مشابه، با کارکرد و وضعیت بدنه لحاظ‌شده — و فهرستی از آگهی‌هایی که پایین‌تر از حد معمول قیمت خورده‌اند." />
<link rel="canonical" href="${siteUrl}/" />
<meta property="og:url" content="${siteUrl}/" />
<meta name="robots" content="index, follow" />
<meta name="color-scheme" content="light dark" />
<meta name="theme-color" media="(prefers-color-scheme: light)" content="#ffffff" />
<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#1d1a16" />
<meta property="og:type" content="website" />
<meta property="og:locale" content="fa_IR" />
<meta property="og:title" content="بازار خودرو باما" />
<meta property="og:description" content="قیمت منصفانه‌ی هر خودرو، و فهرست معامله‌های واقعی." />
<link rel="icon" type="image/svg+xml" href="/favicon.svg" />
<link rel="manifest" href="/site.webmanifest" />
${preload}
<style>
${fontFace}
*,*::before,*::after{box-sizing:border-box}
/* --accent-fg travels with --accent, never assumed to be white: the dark
   scheme's accent is a light green meant for text *on* dark, and white on it
   measures 1.74:1. */
:root{--bg:#faf8f5;--panel:#fff;--text:#1c1917;--muted:#57534e;--border:#e7e2da;
      --accent:#0f6b32;--accent-fg:#ffffff}
@media (prefers-color-scheme:dark){
  :root{--bg:#141210;--panel:#1d1a16;--text:#f5f1ea;--muted:#a8a29e;--border:#332e27;
        --accent:#4ade80;--accent-fg:#14120f}
}
html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:"Vazirmatn Variable",Tahoma,system-ui,sans-serif;
     line-height:1.7;-webkit-font-smoothing:antialiased}
.wrap{max-width:720px;margin:0 auto;padding:48px 20px 64px}
h1{font-size:clamp(26px,6vw,38px);line-height:1.35;margin:0 0 14px;letter-spacing:-.01em}
.lede{font-size:clamp(16px,3.6vw,19px);color:var(--muted);margin:0 0 30px}
.row{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:44px}
.btn{display:inline-block;padding:11px 22px;border-radius:9px;text-decoration:none;
     border:1px solid var(--border);color:var(--text);background:var(--panel);font-size:15px}
.btn-primary{background:var(--accent);border-color:var(--accent);color:var(--accent-fg)}
ul{list-style:none;padding:0;margin:0;display:grid;gap:16px}
li{background:var(--panel);border:1px solid var(--border);border-radius:11px;padding:16px 18px}
li b{display:block;margin-bottom:5px;font-size:15px}
li span{color:var(--muted);font-size:14px}
footer{margin-top:44px;padding-top:20px;border-top:1px solid var(--border);
       color:var(--muted);font-size:13px}
footer a{color:var(--muted)}
</style>
</head>
<body>
<div class="wrap">
<main>
  <h1>قیمت این خودرو منصفانه است؟</h1>
  <p class="lede">
    هر آگهی را با خودروهای واقعاً مشابهش می‌سنجیم — همان مدل، همان تیپ، همان سال —
    و کارکرد و وضعیت بدنه را هم حساب می‌کنیم. نتیجه یک عدد نیست، یک بازه است،
    به‌همراه تعداد آگهی‌ای که آن بازه از رویشان ساخته شده.
  </p>
  <div class="row">
    <a class="btn btn-primary" href="/login">ورود</a>
    <a class="btn" href="/signup">ساخت حساب</a>
    <a class="btn" href="/methodology">روش کار</a>
  </div>
  <ul>
    <li><b>قیمت منصفانه، برای هر خودرو</b><span>
      نه فقط برای آگهی‌های ارزان. اگر خودرویی گران‌تر از حد معمول قیمت خورده باشد،
      همین را می‌گوییم.</span></li>
    <li><b>معامله‌هایی که واقعاً معامله‌اند</b><span>
      تخفیف نسبت به آگهی‌های مشابه، با کنار گذاشتن آگهی‌های اقساطی و حواله‌ای که
      عددشان پیش‌پرداخت است نه قیمت خودرو.</span></li>
    <li><b>وقتی داده کافی نباشد، می‌گوییم</b><span>
      هر عدد با تعداد آگهی پشتش و میزان اطمینانش نمایش داده می‌شود. جایی که
      نمی‌دانیم، حدس نمی‌زنیم.</span></li>
  </ul>
</main>
<footer>
  داده‌ها از آگهی‌های عمومی باما جمع‌آوری می‌شوند. این سایت وابسته به باما نیست.
  <a href="/methodology">روش محاسبه</a>
</footer>
</div>
</body>
</html>`;
}

/**
 * The two pages that render without a session.
 *
 * Listing every route would point a crawler at pages that answer with a login
 * form, which is how a site ends up indexed as a wall of identical sign-in
 * screens.
 */
export function sitemapXml(siteUrl: string): string {
  return `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>${siteUrl}/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>
  <url><loc>${siteUrl}/methodology</loc><changefreq>weekly</changefreq><priority>0.6</priority></url>
</urlset>
`;
}

/**
 * The crawl policy, with the sitemap on the same host as everything else.
 *
 * The `Allow`/`Disallow` split mirrors `AuthRoutes` in App.tsx: the landing
 * page and the methodology page render for a signed-out reader, and every other
 * path redirects to a login form that is not worth indexing.
 */
export function robotsTxt(siteUrl: string): string {
  return `User-agent: *
Allow: /$
Allow: /methodology
# Both of the above render without a session; see App.tsx AuthRoutes.
# Everything else needs a session, so crawling it only produces login pages.
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
