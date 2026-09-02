# Lighthouse

## Where the numbers come from

Measured against the real production image (`Dockerfile.prod` → nginx), not the
dev server, because half of what the audits check is a response header.

    docker build -f Dockerfile.prod -t bama-fe .
    docker run -d --name bama-fe --add-host backend:127.0.0.1 -p 8099:80 bama-fe
    npx lighthouse@12 http://localhost:8099/ --chrome-flags="--headless=new"

`--add-host backend:127.0.0.1` is needed only outside compose: nginx refuses to
start if the `backend` upstream does not resolve.

## Current

| Page | Performance | Accessibility | Best practices | SEO |
| --- | --- | --- | --- | --- |
| `/` (public landing) | **100** | **100** | **100** | **100** |
| `/login` (SPA route) | 94–96 | **100** | **100** | **100** |

`/` is a complete static document with its critical CSS inline and no
JavaScript at all, so it is deterministic: FCP 0.6–0.7s, LCP 1.2s, TBT 0ms
across repeated runs.

`/login` is client-rendered and cannot match that. Its remaining cost is
structural, not an oversight: React has to download, parse and execute before
anything but the HTML shell exists, and Lighthouse's mobile profile applies a
4× CPU slowdown to that work. Closing the last few points means server-side
rendering, which is a different architecture and not worth it for a screen
reached by clicking a button on a page that already scored 100.

## What moved, and by how much

| Change | Effect |
| --- | --- |
| `echarts` → modular registration | chart chunk 1.1 MB → 560 KB |
| Removed the `charts` manual chunk | 560 KB left the initial `modulepreload` set entirely |
| Dropped all `modulepreload` hints | FCP 2.3s → 1.9s — they outranked the one stylesheet that blocks rendering |
| Lazy-loaded `Home` | login stopped downloading the deal-card tree |
| Font subsets declared by hand | 8 files / 196 KB → 3 files / 124 KB |
| Static landing at `/` | FCP 3.4s → 0.6s, LCP 3.8s → 1.2s |
| gzip_static + precompression | 121 KB → 38 KB on the main chunk |
| `/api/auth/me/` 401 → 200 | removed a console error on every cold visit |

Total `dist`: 11 MB → 1.5 MB.

## Two honest caveats

- **This is a lab score.** Real-user numbers from Iran will be worse. The lab
  score is what was asked for and what CI can gate; a real-user timing beacon
  would be the thing to add before claiming anything about field performance.
- **The CI gate asserts 90 for performance, not 100.** A CSR route's score
  moves with runner load, and a gate that flakes is a gate somebody disables.
  Accessibility, best practices and SEO are asserted at 100 because they are
  deterministic.
