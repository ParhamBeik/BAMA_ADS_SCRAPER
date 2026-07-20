# Bama Scraper Agent Notes

Invariants for LLM agents working on this project. Break one and you break the
"snapshot-grabbing machine" contract.

## Navigation (read in this order)

| File | What it owns | Start here for |
|------|-------------|----------------|
| `src/paths.py` | every filesystem path | "where does X live?" |
| `src/store.py` | `bama.db` schema, `open_store`, `upsert_ad`, `mark_inactive`, `counts` | table shapes, lifecycle |
| `src/fetch.py` | HTTP fetch, dim derivation, `AdWriter`, auto-pipeline orchestration | the main loop |
| `src/history.py` | hashing, `record_observation`, change events, `project_lock` | versioning semantics |
| `src/audit.py` | `run_checks`, `--fix` backfill, `--brand-map` | integrity checks |
| `src/analyze.py` | `parse_*` helpers, `group_stats`, `compute_all` | stats math |
| `tests/` | one `test_<module>.py` per src module (except paths); `conftest.py` adds `src/` to `sys.path` | behavior examples |

Every test opens a fresh `open_store(tmp_path / "bama.db")` — copy that pattern for new tests.

## Invariants

- **One database.** `data/bama.db` (opened only via `store.open_store`) is the
  single source of truth. There is no JSON ad tree, no `code_map.db`, no
  `history.db` — do not reintroduce them.
- **`ads.code` is the PK and the index.** brand/model/variant/category are
  denormalized columns, not folders. Grouping/lookup happens in SQL.
- **Append-only history is sacred.** `fetch_runs`, `ad_versions`,
  `ad_observations`, `change_events`: every sighting recorded once per (run, code),
  semantic versions reused (volatile keys `time`/`rank` ignored for versioning),
  repairs use repair origins. Never mutate or delete history rows. Event types:
  `content_changed` (semantic hash differs), `route_changed` (dim grouping moved),
  `reappeared` (gap ≥ 14 days between sightings), plus audit-repair origins.
- **`ads.raw_payload` stays pure Bama JSON.** Scraper bookkeeping goes in columns
  (`pure_ad` strips forbidden keys before storing).
- **Lifecycle:** re-sighting flips `removed` → `active` and clears `removed_at`
  (`upsert_ad`). `mark_inactive(cutoff)` only flips ads with
  `last_seen_ts < cutoff`. An interrupted fetch must NOT run the pipeline, so it
  never marks ads removed on partial data.
- **Auto-pipeline order after a complete fetch:** `mark_inactive` → `audit.run_checks`
  → `analyze.compute_all`. Each step is isolated (try/except) so a failure logs but
  keeps the fetch snapshot. Audit/analyze code stays in their own files; `fetch.py`
  only orchestrates.
- **DB stats only.** `analyze.py` writes rows to `analysis_stats` — no PNGs, no
  matplotlib/seaborn/scipy. Regression is `numpy.polyfit`.
- **Curated data files are frozen.** `data/time_dictionary.json` (relative-phrase →
  timedelta map) and `data/brand_aliases.json` are hand-curated and never mutated
  at runtime. Unknown time phrases are logged once to `data/unknown_times.log` and
  parse to NULL. `audit.py --brand-map` only *bootstraps* alias candidates and can
  over-group distinct brands sharing a Persian acronym (e.g. GMC vs JMC) — a human
  reviews before the file is trusted.
- **Dim derivation is the routing.** `fetch.derive_dims` splits `detail.title` on
  "،" → brand/model, applies `brand_aliases.json`, takes `detail.trim` as variant,
  and detects category by "حواله"/"پیش فروش" markers (default "آگهی ها").
  `clean_name` folds Arabic→Persian chars (ي→ی, ك→ک) and strips diacritics so
  spelling twins land in the same group. Changing any of this regroups ads and
  fires `route_changed` events — intended, but never silent.
- **Standalone project.** No imports from or to `bama-saas/` — the sibling Django
  service re-implements payload rules itself and may *read* `data/bama.db`, but
  this project must never know about it. Own `.gitignore`, `pyproject.toml`, CI.
- **Tests:** `tests/` use a real temp SQLite via `open_store(tmp_path/"bama.db")`,
  no mocking, no services. Keep them CI-safe (`.github/workflows/ci.yml`, py3.10–3.12).
- **`data/.writer.lock`** is the inter-process write lock (`history.project_lock`);
  it is 0 bytes by design and auto-recreated — never delete it in cleanup passes,
  and never write to `bama.db` without holding it in exclusive mode.

## Architecture Change Log

- 2026-06-29 → 2026-07-05 (pre-history, superseded): flat JSON-tree era —
  `ads.json` files routed by `{category}/{brand}/{model}/{variant}` path with
  `code_map.db` as the routing index; collapsed from 9 scripts to 4
  (`fetch`/`audit`/`analyze`/`paths`); "new path wins" relocation + publish-date
  backfill. All replaced by the single-DB design below.

- 2026-07-05: Added append-only `history.db` support for fetch runs, semantic payload versions, per-run sightings, change events, repair events, and legacy baseline seeding. `fetch.py` and `audit.py --fix` now use an exclusive project lock; read-only audit uses a shared lock and reports history health.
- 2026-07-20: Collapsed the JSON tree + `code_map.db` + `history.db` into a single
  `data/bama.db` owned by new `store.py`. The `ads` table is now current-state +
  index (`code` PK, dims as columns). `fetch.py` writes to the DB and runs an
  auto-pipeline (`mark_inactive` → `audit.run_checks` → `analyze.compute_all`) after
  every complete fetch; ad `status`/`removed_at` lifecycle added. `analyze.py` drops
  charts for an `analysis_stats` table (numpy regression; matplotlib/seaborn/scipy
  removed). `audit.py` reduced to DB integrity checks + safe repairs. Added
  `migrate_to_db.py` (one-shot legacy import), a pytest suite in `tests/`, and CI.
- 2026-07-20 (later): Migration executed — 57,262 ads + full history verified in
  `bama.db`; legacy stores (`BAMA ADS/`, `history.db`, `code_map.db`) deleted.
  `migrate_to_db.py`, `test_migrate.py`, and the legacy path constants pruned.
  The project is now DB-only end to end: `python src/fetch.py` is the sole entry
  point for new data.
