# Lighthouse

## Measured against production

    npx lighthouse@12 https://bama-89-106-206-4.sslip.io/ --chrome-flags="--headless=new"

| Page | Performance | Accessibility | Best practices | SEO |
| --- | --- | --- | --- | --- |
| `/` — public landing | **100** | **100** | **100** | **100** |
| `/login` | 95 | **100** | **100** | **100** |
| `/methodology` | 94 | **100** | **100** | **100** |

`/` is a complete static document: FCP 0.9s, LCP 0.9s, TBT 0ms, CLS 0, 20 DOM
elements. It carries no JavaScript at all, which is why it is the one page that
scores 100 on performance and why it does so repeatably.

`/login` and `/methodology` are React routes. Their remaining 5–6 points are
one thing: the browser cannot paint until 14 KB of render-blocking CSS has
arrived, and then React has to download, parse and execute before there is
anything to show — with Lighthouse's mobile profile applying a 4× CPU slowdown
to all of it. FCP 2.0s, LCP 2.2–2.3s.

## What was tried and rejected

Two changes measured worse and were reverted rather than kept for the sake of
the number:

- **Rendering optimistically from a stored session hint**, so the tree could
  paint before `/api/auth/me/` returned. React then rendered twice: blocking
  time went from 10ms to 340ms and first paint moved *later*. 94 → 81.
- **Loading the stylesheet with `media="print"` and flipping it in a script.**
  First paint improved (2.0s → 1.5s) and largest paint regressed (2.2s → 2.7s)
  with CLS appearing at 0.01, because the real content then had to wait for CSS
  that was no longer prioritised. Net zero, plus a CSP carve-out and a second
  request to maintain.

The honest way to close the gap is server-side rendering, which is a different
architecture. It is not worth it for two routes reached by clicking a button on
a page that already scores 100.

## What moved

| Change | Effect |
| --- | --- |
| Static landing at `/` | FCP 3.4s → 0.9s, LCP 3.8s → 0.9s |
| `echarts` → modular registration | chart chunk 1.1 MB → 560 KB |
| Removed the `charts` manual chunk | 560 KB left the initial `modulepreload` set |
| Dropped all `modulepreload` hints | FCP 2.3s → 1.9s — they outranked the one stylesheet that blocks rendering |
| Lazy-loaded `Home` | login stopped downloading the deal-card tree |
| Font subsets declared by hand | 8 files / 196 KB → 3 files / 124 KB |
| `sourcemap: true` → split bundle | 8.7 MB of maps → 1.5 MB total `dist` |
| gzip_static + build-time precompression | 121 KB → 38 KB on the main chunk |
| `/api/auth/me/` 401 → 200 | removed a console error on every cold visit |
| `code` given a font-size floor | 50% → 100% legible text on `/methodology` |
| Capped the model-card history | 5,190 → 1,993 DOM elements, and it no longer grows nightly |

## Reproducing locally

    docker build -f Dockerfile.prod -t bama-fe .
    docker run -d --name bama-fe --add-host backend:127.0.0.1 -p 8099:80 bama-fe

`--add-host backend:127.0.0.1` is needed only outside compose: nginx refuses to
start if the `backend` upstream does not resolve. Measure against the image
rather than the dev server — half of what the audits check is a response header,
and `npm run dev` sets none of them.

## Two honest caveats

- **This is a lab score** under synthetic throttling. Real-user numbers from
  Iran will be worse. A real-user timing beacon is the thing to add before
  claiming anything about field performance.
- **The CI gate asserts 90 for performance, not 100.** A client-rendered
  route's score moves with runner load, and a gate that flakes is a gate
  somebody disables. Accessibility, best practices and SEO are asserted at 100
  because they are deterministic.
