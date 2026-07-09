# Bama Scraper Agent Notes

- Keep this project flat: `src/` scripts plus `data/`.
- Preserve the canonical path and `code_map.db` invariants.
- Preserve append-only `history.db` invariants: every live fetch sighting is recorded once per run/code, semantic versions are reused, and repairs use repair origins.
- Use `audit.py --fix` when path logic or brand aliases change.
- Keep `ads.json` payloads pure; do not reintroduce scraper metadata.
- Update this file when scraper architecture changes.

## Architecture Change Log

- 2026-07-05: Added append-only `history.db` support for fetch runs, semantic payload versions, per-run sightings, change events, repair events, and legacy baseline seeding. `fetch.py` and `audit.py --fix` now use an exclusive project lock; read-only audit uses a shared lock and reports history health.
