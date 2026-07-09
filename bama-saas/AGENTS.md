# Bama SaaS Agent Notes

- This project is backend-only for now; defer frontend work.
- Keep PostgreSQL schema, Alembic migrations, and FastAPI docs aligned.
- Live fetchers should reuse the scraper normalization and time parsing rules.
- Prefer query-friendly normalized columns plus `raw_payload` JSONB.
- Update this file when backend architecture changes.

## Architecture Change Log

- 2026-07-05: Rebuilt the backend around Alembic-managed PostgreSQL tables, live Bama ingestion, immutable sightings, change-only price history, DB-native audits, protected tracked background jobs, and public catalog/history/insight APIs. Removed the stale frontend, pandas analytics, cross-project fetch-core copy, and startup `create_all()`.
- 2026-07-05: Added append-only payload versioning and classified change events. Observations now link to immutable semantic versions, repeated content reuses versions, reverted content creates a visible transition, audits check version/event integrity, and history APIs expose versions, changes, and combined timelines.
