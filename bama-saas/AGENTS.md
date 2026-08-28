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
- `ui/web/` — Tailwind v4 + shadcn/ui. Five destinations behind one floating
  header (`components/AppHeader`), no sidebar. `Home` is the market pulse,
  `Analyse` is one page whose scope runs market → brand → model → trim → model
  year in the URL, `Deals`, `Explorer`, `Saved`, plus staff-only `Control` in
  the account menu.

## The stylesheet

`src/styles.css` is the whole colour contract and it is layered deliberately:

- The two `:root` blocks are the only place a colour is chosen. `@theme inline`
  republishes them as utilities under both our names (`bg-panel`, `text-up`) and
  the ones shadcn's generated components reach for (`bg-card`,
  `text-muted-foreground`). `inline` is load-bearing — without it a utility
  freezes whichever theme was active at build time instead of re-resolving.
- Note shadcn's `accent` is a *hover surface*; our brand colour is its `primary`.
- Dark mode is `data-theme` on `<html>`, not a class, taught to Tailwind via
  `@custom-variant`. `theme.tsx` writes that attribute **synchronously in the
  setter**, not only in an effect: the charts read their palette out of CSS
  variables during render, so an effect alone left the first frame after a
  switch drawing the outgoing theme's colours.
- `--up`/`--down` mean direction and `--warn` means a confidence or staleness
  warning; none may be used decoratively. The accent must stay ≥30° of hue from
  all three — a constraint contrast ratio cannot see, since two colours of equal
  lightness always score ~1.0 against each other. `npm run check:contrast`
  parses the tokens out of the stylesheet and fails on a broken ratio or hue
  gap; it is the palette's test, so run it after touching a token.
- Tailwind's reset zeroes every margin and padding, including on elements some
  screens still render bare. The `@layer base` block restores those browser
  defaults; delete it only once no screen depends on them.

`db_table` is pinned on every model (`catalog_*`, `history_*`, `market_*`,
`analytics_*`). Moving a model between files is therefore free — the physical
schema is independent of the Python layout. Do not remove those pins.

## Domain invariants

- **Nothing shared may hold a gated response.** A gated endpoint's *reply* must
  never be stored anywhere a second reader could be handed it — that turns one
  signed-in request into a key anybody can reuse until it expires. Two shapes of
  this, both of which have bitten: `@cache_page`, which wraps the view from
  outside so a hit returns before DRF runs its permission check at all; and
  `Cache-Control: public`, which invites any proxy or CDN in front to keep its
  own copy. Cache the *answer* instead — `views.cached(key, seconds, produce)`
  holds the payload behind the gate — and say `private` on gated responses.
  `tests/test_api.py::test_a_warm_cache_is_not_a_way_past_the_gate` fails the
  moment any of the six analytics endpoints goes back to a response cache.
- **Cache keys are hashed, never composed.** `views.cache_key(prefix, parts)`.
  Brand slugs are user-facing text and `ingest` can mint one containing spaces,
  which Django's Redis backend warns about on every get and memcached rejects.
- **The read throttle is flat, not scoped.** Production (`if not DEBUG` in
  `config/settings.py`) applies `AnonRateThrottle`/`UserRateThrottle` globally at
  60/min anon and 600/min user — do not "add throttling", it is already there,
  and `listing_image` opts out with `@throttle_classes([])` because one scroll
  outruns the shared rate. What it is not is *scoped*: one rate covers a catalog
  lookup and the `distribution`/`movement` scans alike, so a caller varying the
  scope never reuses a cached answer and can re-run the expensive query 600 times
  a minute while staying inside the limit. Validating scope ids does not help
  (the real catalogue already dwarfs the cache); a scoped rate on those two
  endpoints would. Weigh it against the SPA, which fires them per keystroke.
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
  adjustment, four-band body-condition stratum (or a catalogue-wide measured
  haircut when a band/bucket is thin), no regression. Below 8 peers it refuses
  to answer rather than quoting a median of three cars. `MIN_ASK_VS_MEDIAN = 0.5`
  drops asks below half the peer median as deposits or typos, as do حواله titles
  and the 10M-toman sentinel. Age is *not* multiplied into the score — it orders
  the board instead (below). Recency-weighting the median was measured and
  rejected (0.07% move, 48% cohort loss).
- **The board is banded by freshness, then by discount.** Recency is
  `Ad.publish_at` (Bama's own phrase, which also moves when a seller bumps),
  never `first_seen_at` — that says when our crawler arrived, so a deep backfill
  would make a year-old listing rank as new. `pricing.deal_window()` measures
  the discount floor from the current board and **stops widening at 7 days**.
  A short board is the honest signal that today has few good buys; widening
  to refill after scoring got stricter would put old listings back. It is
  cached in Redis and **dropped by `compute_deal_scores`**. The same window
  applies to `band=review` and `band=all`, not only `top`.
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
- **The feed filters belong on the data request.** `FEED_FILTERS` goes on every
  `fetch_page` call, not just `WARMUP_URL` — the warm-up only sets cookies. They
  lived there alone for a long time, so every sweep paged the *unfiltered* feed:
  measured on page 3, 4 of 30 ads had no photo without them and 0 of 30 with.
  That is where the 8,889 deleted rows came from, and why `_photo_missing` was
  still quarantining ~2.3k ads a day after it shipped. Spell them `=1`; the API
  answers 500 to `=true`.
- **Thumbnail and gallery photo 0 are different files.** `primary_image_url` is
  the CDN's `resize,w_450` upload and `image_urls[0]` is its `w_600` one, so
  they get separate proxy addresses (`/thumb/` vs `/<n>/`). Addressing both
  through one index served the large file to all 24 cards on a grid page.
- **`Ad.url` is a path, not a URL.** Every row in production stores
  `/car/detail-...`, so anything rendering it into an href resolves against our
  own origin. `parsing.absolute_ad_url` is the only place that fixes it — the
  notifier used to carry a private copy, which is why the Telegram alerts worked
  and the website never did.
- **Turnover is a completed-window rate, not a mean time-to-sell.**
  `research.turnover` counts what share of a model's listings left the feed
  within N days, over episodes that *started* at least N days ago — so every
  listing in the denominator had the full window in which to go, and there is
  nothing to censor. A mean over finished listings is the biased number
  `survival` deliberately keeps as `naive_mean_days_finished_only` to show how
  wrong it is; do not rank models by it. The window must also fit inside
  `BAMA_EPISODE_CLEAN_START`, or the answer is `window_exceeds_clean_history` —
  a distinct reason from "too few listings", because that one fixes itself as
  history accrues and a shorter window works today.
- **Per-brand and per-model index series already exist.** `jobs.market_index`
  has written them every warm tick for every scope with at least
  `MIN_SCOPE_COHORTS` live cohorts since the index shipped; for a long time
  nothing read anything but `scope=market`. Ranking them (`research.movers`) is
  a query, not a computation — do not add a pipeline step for it. Anything
  *finer* than model (a trim, one model year) is computed per request from the
  same snapshots via `cohort_series(..., variant_id=, year_jalali=)`, because
  persisting a series per trim would multiply the warm tick's writes for a
  question most sessions never ask.
- **Every leaderboard row carries its sample.** `movers` and `turnover` both
  return the cohort/ad/episode counts behind each figure and the UI prints them.
  A 4% move off three cohorts and one off forty are not the same claim, and a
  board that shows only the percentage is one the thinnest scope wins.
- **User-facing prose is composed in the UI, not the API.** Serializers return
  machine keys and facts (`reason`, `cohort_flags`, a component's `facts` dict);
  `ui.tsx:humanReason`, `FLAG_LABEL` and `Explorer.componentDetail` turn them
  into Persian. `fair_price` used to build its component `detail` as an English
  sentence, which shipped "median of 13 peers" into a Persian table on the one
  panel whose job is to make the number checkable.
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
- **Why an ad left is inferred, except when we opened the page.** Bama's feed
  carries no reason, so `likely_reason` / `reason_confidence` stay out of
  `status`. Tenure vs the P90 of `ListingEpisode` is a guess (medium). A
  detail-page probe of the bargain board that gets HTTP 410 / "این آگهی فروخته
  شد" is an observation: `REMOVED`, reason sold, confidence high, immediately.
  That probe must persist a `FetchRun` with `stop_reason=blocked` on 403 or it
  would hammer an IP ban while the feed crawler sits in cooldown.
- **Anything credible may lower the depth ratchet, not just a full sweep.**
  `known_feed_depth` is a one-way high-water mark and the feed shrinks daily, so
  something has to bring it down. Requiring `mode=FULL` for that was the bug that
  froze removal detection: rolling coverage retired the full sweep, so in one
  week production logged 624 backfills that reached the real end of the feed and
  0 full sweeps. `end_of_feed_is_credible` is the guard — a bounded run also
  needs an existing ceiling to be measured against, so a cold database cannot be
  taught a bogus depth by one empty page.
- **A disbelieved end-of-feed is still recorded, and agreement eventually wins.**
  The 5% bounded bar is right for organic drift (measured: under 1% a day), but
  nothing could ever clear it after a *step* change — on 2026-08-25 adding
  `image=1&priced=1` to the data request cut the feed from ~34,500 ranks to
  ~28,710, a 192-page shortfall against a 57-page tolerance. Every coverage run
  then walked to page 957, was disbelieved, wrote no `PageCoverage` **and
  discarded the observation**, so the identical gap was re-derived ten minutes
  later. Removal detection froze for a day. The fix is not a looser bar: a
  disbelieved empty page now persists `feed_end_rank` under
  `StopReason.END_UNCONFIRMED` (with `reached_end` still False, so the ratchet
  ignores it), and `end_is_corroborated` believes any depth that
  `END_AGREEMENT_RUNS` recent runs independently found within
  `END_AGREEMENT_RATIO` of each other. Size, not agreement, is what a spurious
  blip can fake. Corroboration only *lowers* an existing ceiling — a cold
  database still cannot be taught a depth by empty pages alone.
- **`max_ads` on delta is a backstop, not the intended stop.** The saturation
  rule (`max_stale_pages` consecutive pages with nothing new) is what should end
  a delta, so a busy feed is followed as deep as it is still moving. At
  `BAMA_WORKER_FETCH_ADS = 500` it was the routine stop instead: 365 runs ended
  on `max_ads` at exactly 17 pages against 228 on `stale_pages` at ~8.8, i.e.
  the crawl walked away from a moving feed 62% of the time. Raised to 1500.
  If runs still stop on `max_ads` often, raise it again — that reading is the
  signal, not the page count.
- **Coverage backfill must not stop early on staleness.** Its job is to *prove*
  a rank range was looked at, not to find new ads; a saturation stop would leave
  the gap open and `coverage_is_complete` false, which is the same freeze by
  another route. Only `mode=DELTA` gets the stale-page rule.
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
