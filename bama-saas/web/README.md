# Bama Market Intelligence — web

React + Vite + TypeScript. Five workspaces: Market Overview (public), Buyer
Explorer, Research (subscription), My Market, Operations (staff).

```bash
npm install
npm run dev        # proxies /api to http://localhost:8001
npm run build
npm run typecheck
npm run api:types  # regenerate src/api/schema.d.ts from the Django OpenAPI schema
```

## Three things worth knowing before changing anything

**The API types are generated, not written.** `src/api/schema.d.ts` comes from
the Django OpenAPI schema. Re-run `npm run api:types` after a backend change and
a renamed or removed endpoint becomes a compile error rather than a blank panel
someone notices in production.

**Filter state lives in the URL.** `useFilters` reads and writes search params, so
every view is shareable and the back button works. Two panels reading the same
filter cannot disagree, because there is only one copy of it.

**Provenance is not decoration.** Every research answer arrives with `as_of`,
coverage and a methodology version, and `<Provenance>` renders them. These
numbers come from a crawl that can be incomplete, and a survival curve computed
across a coverage hole reads crawler downtime as cars leaving the market. If you
add a panel, render its envelope.

`<Async>` handles loading, error, empty, auth-required, subscription-required and
*unavailable* — the last being the backend refusing to compute a number from too
little data. That is a real answer, not an error, and never an empty chart.
