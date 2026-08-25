# Agent notes

`README.md` describes what the app is and how to run it. This file is only the
things that are expensive to rediscover and cheap to break. Update it when one
of these decisions changes.

## Shape

One module per concern, no `services/` packages, no per-command modules:

- `apps/core/` — `models.py` (all 15 models, four commented sections),
  `views.py`, `serializers.py`, `filters.py`, plus the analytics:
  `pricing.py` (fair price + deal board + the board's dynamic window),
  `quality.py` (the read-side filters), `research.py` (index, survival,
  depreciation), `notify.py`, `images.py` (the Redis-cached photo proxy).
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
  10M-toman sentinel. Age is *not* multiplied into the score — it orders the
  board instead (below).
- **The board is banded by freshness, then by discount.** Recency is
  `Ad.publish_at` (Bama's own phrase, which also moves when a seller bumps),
  never `first_seen_at` — that says when our crawler arrived, so a deep backfill
  would make a year-old listing rank as new. `pricing.deal_window()` measures
  both thresholds from the current board rather than hardcoding them: it widens
  the window a day at a time until `MIN_CANDIDATES` listings clear that width's
  own 75th-percentile discount. It is cached in Redis and **dropped by
  `compute_deal_scores`** — the window is computed from exactly the rows a
  rebuild deletes.
- **`TRUSTED_MAX_DISCOUNT = 25.0` splits recommend from review.** Above it the
  gap is an attribute the (model, variant, year) key cannot see far more often
  than a bargain. Production carries 272 such rows against 8,803 below it, which
  is a systematic signal, not a tail — see the open question in the README.
  The constant lives in `pricing.py` because the API filters on it and the UI
  narrates it; it used to live only in `Deals.tsx`.
- **Installment ads are not cheap cars.** Their lump-sum price field holds a
  down payment. Anything ranked by price gap must exclude them
  (`quality.price_basis_unclear`) or the top of the board is entirely artifact —
  it was 74% of the top 200 rows.
- **Photos come from the payload, and the payload is the whole payload.**
  The gallery is `payload["images"]`, one level *above* `detail`;
  `_image_urls` was handed `detail` alone, so `_MAX_GALLERY` never applied and
  no ad in production had more than one photo. `detail.image` is a *fallback*,
  never appended — it is `images[0]` at another width, so adding it to a real
  gallery duplicates the first photo. `backfill_images` refills from
  `raw_payload` with no network; 45% of the catalog was photoless and 78% of
  those had a usable URL already stored.
- **A photoless ad is not stored.** `image=1&priced=1` is what the crawl asks
  for, so no photo means the row is out of population — `_photo_missing` is a
  HARD rule, and `backfill_images` deletes what it cannot fill (fill first,
  delete second; reversed it would destroy the ~28.5k rows whose photos were
  merely unread). Test fixtures must therefore carry an `images` block —
  `tests/conftest.gallery()` builds one.
- **`Ad.url` is a path, not a URL.** Every row in production stores
  `/car/detail-...`, so anything rendering it into an href resolves against our
  own origin. `parsing.absolute_ad_url` is the only place that fixes it — the
  notifier used to carry a private copy, which is why the Telegram alerts worked
  and the website never did.
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
- **Three states, because there are three facts.** `ACTIVE` (seen), `REMOVED`
  (provably absent), `UNVERIFIED` (absent, but coverage could not be proven).
  The third exists because leaving unprovable ads `ACTIVE` had the app
  advertising 546 cars nobody had seen in 48 hours. `UNVERIFIED` is a holding
  state: the next complete pair of windows resolves it either way.
- **Why an ad left is inferred, never observed.** Bama's feed carries no reason,
  so `likely_reason` / `reason_confidence` are kept out of `status` — the status
  column says what we saw, those say what we guess. The "likely expired"
  threshold is the P90 of measured `ListingEpisode` tenure, never a constant.
- **Anything credible may lower the depth ratchet, not just a full sweep.**
  `known_feed_depth` is a one-way high-water mark and the feed shrinks daily, so
  something has to bring it down. Requiring `mode=FULL` for that was the bug that
  froze removal detection: rolling coverage retired the full sweep, so in one
  week production logged 624 backfills that reached the real end of the feed and
  0 full sweeps. `end_of_feed_is_credible` is the guard — a bounded run also
  needs an existing ceiling to be measured against, so a cold database cannot be
  taught a bogus depth by one empty page.
- **Reposts are linked, never merged.** A relist gets a fresh Bama code, so
  `listing_fingerprint` (content identity, price deliberately excluded) ties the
  pair together via `Ad.reposted_from`. Both rows stay; a wrong link is one
  `UPDATE` to undo.
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
