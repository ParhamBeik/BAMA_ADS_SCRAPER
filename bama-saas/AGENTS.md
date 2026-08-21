# Agent notes

`README.md` describes what the app is and how to run it. This file is only the
things that are expensive to rediscover and cheap to break. Update it when one
of these decisions changes.

## Shape

One module per concern, no `services/` packages, no per-command modules:

- `apps/core/` — `models.py` (all 15 models, four commented sections),
  `views.py`, `serializers.py`, `filters.py`, plus the analytics:
  `pricing.py` (fair price + deal board), `quality.py` (the read-side filters),
  `research.py` (index, survival, depreciation), `notify.py`.
- `apps/jobs/` — `parsing.py` (no Django import), `fetcher.py` (HTTP + crawl
  gate + coverage arithmetic), `ingest.py`, `verify.py`, `jobs.py` (one function
  per job, each returning a dict), `pipeline.py` (which jobs, in what order),
  `management/commands/bama.py` (the only command).
- `config/settings.py` — one file. `DJANGO_DEBUG=1` selects the local profile;
  the default is hardened, so a missing env var cannot fail open.

`db_table` is pinned on every model (`catalog_*`, `history_*`, `market_*`,
`analytics_*`). Moving a model between files is therefore free — the physical
schema is independent of the Python layout. Do not remove those pins.

## Domain invariants

- **`Ad.year` is provenance, never a key.** Bama publishes model years in both
  calendars, so raw `year` mixes 1399 and 2025 in one column. Cohorts and
  `?year=` use `year_jalali` (index `ad_market_jy_idx`). The `+621` offset in
  `parsing.py` applies to *model years* only, never to a timestamp.
- **Zero is a value.** `detail.mileage` is `"صفر کیلومتر"` for ~33% of ads.
  Use `parse_mileage` (returns `0`), never a positive-only parser.
- **Verify, then quarantine.** Every ingest runs the rules in `verify.py`. Soft
  rule ids land in `Ad.quality_flags` and must not drop otherwise-good data; a
  hard failure (`HARD_RULE_IDS`) writes an `IngestReject` and deletes the `Ad`.
  Analytics reads go through `quality.verified` / `verified_by_ad` /
  `without_cohort_outliers` — never a raw `Ad.objects` queryset.
- **Fair price *is* the deal board.** `MIN_PEERS = 8`, bucket-median mileage
  adjustment, no regression. Below 8 peers it refuses to answer rather than
  quoting a median of three cars. `MIN_ASK_VS_MEDIAN = 0.5` drops asks below
  half the peer median as deposits or typos, as do حواله titles and the
  10M-toman sentinel. Age is reported, not multiplied in.
- **Installment ads are not cheap cars.** Their lump-sum price field holds a
  down payment. Anything ranked by price gap must exclude them
  (`quality.price_basis_unclear`) or the top of the board is entirely artifact —
  it was 74% of the top 200 rows.
- **Append-only provenance.** `AdVersion` dedups on the *semantic* hash
  (volatile payload paths excluded), `AdObservation` is one row per run per ad,
  `PriceObservation` is one row per actual price change, not per sighting.
  Re-import is idempotent for everything except `AdObservation`.

## Crawl invariants

- **`pageIndex` is 0-based** (`FIRST_PAGE = 0`), 30 ads per page.
- The feed is strictly recency-ordered, `rank = 30 * page + 1..30`, and ends on
  an empty page. There is no total-count field, which is why coverage has to be
  reconstructed from rank intervals.
- Insertions push ads to *higher* ranks — a harmless re-read. Deletions pull ads
  to *lower* ranks, past pages already read, which is silent loss. `PageCoverage`
  and the `coverage` job exist for exactly that.
- **Delta never resumes from a checkpoint**; it always restarts at page 0. Only
  `full` and `backfill` resume.
- **Removal is proven, not guessed.** An ad goes `REMOVED` only after two
  complete coverage windows without it (`COVERAGE_WINDOW_HOURS = 24`). Elapsed
  wall-clock time is not evidence.
- **A 403 is an IP block, not a rate limit.** The crawl gate opens a cooldown
  breaker rather than probing; slowing the crawl does not help and refetching
  during a block poisons coverage.

## Pipeline

Steps are deliberately independent: a flaky fetch must not stop the cheap local
steps from keeping analytics fresh. The exceptions are declared in `DEPENDS_ON`
— `market_index` after `snapshot`, because a chained index extended over a
missing snapshot reports the crawler's downtime as a market move. A step whose
prerequisite failed is recorded `skipped`, which is distinct from both success
and silence.

Every step records a `JobRun` either way. Only the network fetch is retried.
