# Bama — web UI

React + Vite + TypeScript. Local personal deal finder: seven screens, no
login.

| Path | Page |
| --- | --- |
| `/` | Deal board |
| `/explore` | Catalog explorer |
| `/listing/:code` | Listing detail |
| `/market` | Market overview / index |
| `/research/:modelId` | Kaplan–Meier time-to-sell + year retention |
| `/saved` | Saved cars (user-less favorites) |
| `/control` | Crawl health + jobs |

```bash
npm install
npm run dev        # proxies /api to http://localhost:8001
npm run build
npm run typecheck
npm run api:types  # regenerate src/api/schema.d.ts from the Django OpenAPI schema
```

## Three things worth knowing before changing anything

**The API types are generated, not written.** `src/api/schema.d.ts` comes from
the Django OpenAPI schema. Re-run `npm run api:types` after a backend change
and a renamed or removed endpoint becomes a compile error rather than a blank
panel.

**Filter state lives in the URL.** `useFilters` reads and writes search params,
so every view is shareable and the back button works.

**Provenance is not decoration.** Research answers arrive with `as_of`,
coverage and a methodology version; `<Provenance>` renders them. A survival
curve computed across a coverage hole reads crawler downtime as cars leaving
the market. `<Async>` handles loading, error, empty, and *unavailable* (the
backend refusing to compute from too little data). That is a real answer,
not an error, and never an empty chart.
