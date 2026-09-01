# Agent notes

`README.md` describes what the app is and how to run it. This file is only the
things that are expensive to rediscover and cheap to break. Update it when one
of these decisions changes.

## Shape

One module per concern, no `services/` packages, no per-command modules:

- `apps/core/` — `models.py` (all 15 models, four commented sections),
  `views.py`, `serializers.py`, `filters.py`, plus the analytics:
  `pricing.py` (fair price + deal board + the board's dynamic window),
  `quality.py` (the read-side filters *and* the one condition-band rule ladder),
  `research.py` (index, segments, survival, depreciation, the market read, the
  budget search), `notify.py` (the operator channel *and* per-user alerts),
  `images.py` (the Redis-cached photo proxy).
- `apps/accounts/` — user, session auth, and the per-user layer: `Favorite`,
  `Watchlist`, `AlertRule`, `AlertDelivery`. The last three share the
  `ScopedToACar` abstract base.
- `apps/ml/` — the learned layer, one module per concern: `features.py` (the
  design matrix, shared by training and inference), `metrics.py` (pure judgement
  functions), `train.py` (five fits), `registry.py` (artifacts + the promotion
  gate), `inference.py` (batch scoring), `monitoring.py` (drift), `views.py`.
  Everything in it degrades to a refusal when the `ml` extra is not installed.
- `apps/jobs/` — `parsing.py` (no Django import), `fetcher.py` (HTTP + crawl
  gate + coverage arithmetic), `ingest.py`, `verify.py`, `jobs.py` (one function
  per job, each returning a dict), `pipeline.py` (which jobs, in what order),
  `management/commands/bama.py` (the only command).
- `config/settings.py` — one file. `DJANGO_DEBUG=1` selects the local profile;
  the default is hardened, so a missing env var cannot fail open.
- `ui/web/` — Tailwind v4 + shadcn/ui. Five destinations behind one floating
  header (`components/AppHeader`), no sidebar. `Home` is the market pulse,
  `Budget` is the "what can this much money buy" path, `Analyse` is one page
  whose scope runs market → brand → model → trim → model year in the URL,
  `Deals`, `Explorer`, plus staff-only `Control` in the account menu. The two
  *personal* surfaces — `Saved` and `Alerts` — are icons in the right cluster
  rather than tabs: seven Persian labels clip mid-word in the phone tab row, and
  the alert badge has to be visible from every screen anyway.

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
  own copy. Cache the *answer* instead — `views.cached` / `views.cached_answer`
  hold the payload behind the gate. `listing_image` is the only endpoint that
  sets `Cache-Control` at all, and it says `private`; if you ever add the header
  elsewhere, say `private` there too. `test_a_warm_cache_is_not_a_way_past_the_gate`
  covers the response-cache half on all six cached endpoints — the header half is
  a rule, not a test.
- **Coverage ages with the answer it qualifies.** Both come out of
  `views.cached_answer` as one entry. Cached apart they drift, and a coverage
  reading taken after the crawl closed a gap ends up badging a figure computed
  while it was open — "complete sweep" printed on the one number the hole is the
  reason to doubt.
- **Cache keys embedding user text are hashed.** `views.cache_key(prefix, parts)`.
  Brand slugs are user-facing text and `ingest` can mint one containing spaces,
  which Django's Redis backend warns about on every get and memcached rejects.
  Fixed literals (`"markets:summary"`) and alphanumeric ad codes are fine as-is.
- **Bound every scope axis that is not a row.** Brand, model and variant name
  rows, so the catalogue's size bounds how many distinct cache entries exist. A
  year does not — unbounded it is any integer, and each one is a fresh key that
  misses the cache and re-runs the scan behind it. `views._model_year` refuses
  anything outside `verify`'s `MIN_JALALI_YEAR`/`MAX_JALALI_YEAR`. Do not extend
  this to existence checks on the row-backed axes: that costs a query per request
  and buys nothing, since the real catalogue already dwarfs the cache.
- **The read throttle is flat, not scoped.** Production (`if not DEBUG` in
  `config/settings.py`) applies `AnonRateThrottle`/`UserRateThrottle` globally,
  defaulting to 60/min anon and 600/min user via `THROTTLE_ANON`/`THROTTLE_USER`
  — do not "add throttling", it is already there, and `listing_image` opts out
  with `@throttle_classes([])` because one scroll of photos outruns the shared
  rate. What it is not is *scoped*: one rate covers a catalog lookup and the
  `distribution`/`movement` scans alike, so a signed-in caller varying the scope
  never reuses a cached answer and can re-run the expensive query 600 times a
  minute while staying inside the limit. (The anon rate does not enter into it —
  permissions are checked before throttles, so an anonymous caller is refused
  first.) A scoped rate on those two endpoints would close it; weigh it against
  the SPA, which fires them per keystroke.
- **`Ad.year` is provenance, never a key.** Bama publishes model years in both
  calendars, so raw `year` mixes 1399 and 2025 in one column. Cohorts and
  `?year=` use `year_jalali` (index `ad_market_jy_idx`). The `+621` offset in
  `parsing.py` applies to *model years* only, never to a timestamp.
- **A make is a make; a model is not.** Bama files سمند, پراید, دنا and their
  siblings as top-level brands, which split one manufacturer's catalogue into a
  dozen one-model "brands" and made the brand filter useless for the two makes
  that dominate the market. `ingest.BRAND_PARENT` remaps them to ایران خودرو
  and سایپا on the way in; migration `0025` merged the rows that already
  existed. The model names carry the identity that is lost («دنا پلاس», «پراید
  ۱۳۱»), so nothing became ambiguous. فونیکس is deliberately left alone.
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
- **The discount the reader sees is measured against the peer median.** The
  badge used to be computed from `fair_value` while the card printed
  `peer_median` beside it, so the arithmetic on screen never reconciled — the
  one panel whose job is to be checkable was the one that could not be checked.
  The condition and mileage adjustments are still computed and still shown, but
  as *context* for a gap, not as the gap itself. This re-ranks the board.
- **Condition never raises a car's value.** The measured-haircut ladder is
  ordered (`CONDITION_BANDS`: clean → cosmetic → painted → structural), and a
  thin band
  used to be filled from same-band peers, which let a repainted car out-price a
  clean one whenever its own peer group happened to be dearer. There is now one
  pooled path, and `pricing._monotone` forces the measured ladder to be
  non-decreasing before it is applied — a sampling accident cannot invert it.
- **A damaged car is reviewed, not recommended.** Scoring on the peer median
  reopened the old failure where the board filled with paint and structural
  repair, so `pricing.REVIEW_CONDITION_BANDS` routes those two bands to
  `band=review` through the real column `DealScoreCache.needs_review`. It is a
  column and not a key inside `components` on purpose: `exclude()` on a JSON
  path also drops every row where the key is absent, because SQL `NOT NULL` is
  not TRUE.
- **Every screen counts the same population.** `pricing.scorable_rows()` is it —
  verified, non-outlier, clear-basis ads. The browse endpoints each used to
  assemble their own queryset and skipped `exclude_unclear_price`, so the
  Explorer, the front-page tile, the market summary and the model search
  reported four different totals for one catalogue. `Ad.price_basis_unclear` is
  a derived, indexed column written in `Ad.save`; no caller sets it.
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
- **A thinly-crawled day is bridged, not chained through.** A day whose ad count
  falls under `research.MIN_DAY_COVERAGE_RATIO` of its trailing seven-day median
  is a crawl outage, and chaining a return across it reported our own downtime
  as a market move. Such a day publishes `return_pct: null` and `low_coverage`,
  and — this is the load-bearing half — does *not* become the previous point, so
  the next well-covered day links back to the last well-covered one and the
  chain stays continuous. `gap_days` says how far it reached back. `movers`
  samples the last non-thin point, so one thin day at the end cannot erase a
  scope from the board.
- **Every leaderboard row carries its sample.** `movers` and `turnover` both
  return the cohort/ad/episode counts behind each figure and the UI prints them.
  A 4% move off three cohorts and one off forty are not the same claim, and a
  board that shows only the percentage is one the thinnest scope wins.
- **"What changed" and "what is turning" are two questions, so they get two
  metrics.** `movers` still *ranks* on `change_pct`, the two-point chord across
  the window, because that is literally what the reader asked — and a test
  (`test_movers_ignores_dates_outside_the_window`) pins it: a series that rose
  for ten days and then sat flat for thirty still *changed* over the window.
  What was missing is direction *now*, so every row also carries a Theil–Sen
  slope (median of all pairwise slopes, so one bad day cannot set the rank),
  `slope_agreement` (the share of pairs sharing the median's sign — a fitted line
  through noise otherwise reads as a trend), a 7-day `recent_slope_pct`, and
  `turning` when the two disagree in sign. The turns get their own list, sorted
  by the recent slope. Do not collapse these back into one number.
- **A segment index measures prices, not reclassification.** `MarketIndex.Scope`
  now carries `PRICE_BAND`, `YEAR_BAND` and `BODY_TYPE` beside brand and model,
  and `research.cohort_segments()` fixes each cohort's band membership **from the
  latest snapshot** rather than recomputing it per day. Left to drift, a cohort
  whose price crossed `PRICE_BAND_EDGES` would leave one band and join another,
  and the two series would each print a move that was only a car changing
  shelves. Same argument for `YEAR_BAND_EDGES` as cars age past 3/7/15 years.
- **The market read is a composition, and it ships its inputs.**
  `research.market_read()` crosses the index's price direction with flow
  (absorption = departures ÷ arrivals) into one categorical position, and returns
  all three so the reader can disagree with the synthesis. Two guards: a
  `ABSORPTION_DEAD_BAND` of 5% around parity, because balanced flow is the normal
  state and calling every 1% wobble a squeeze is noise; and `MIN_FLOW_EPISODES`,
  below which flow is `unknown` rather than computed — the position then reads
  from price alone and says so.
- **The condition-band ladder has exactly one definition.** `_BAND_RULES` in
  `quality.py` is ordered and first-match-wins, and that ordering is load-bearing
  («صافکاری بدون رنگ» contains «بدون رنگ» contains «رنگ»). `condition_band()`
  walks it in Python; `condition_band_q()` *derives* the SQL predicate from the
  same list — for each position the band occupies, match that rule and exclude
  every earlier one. `filters.py` used to carry a hand-written copy of the regex
  ladder, which is how the two drift. An unknown band returns `Q(pk__in=[])`, so
  a typo selects nothing rather than everything.
- **A conditioned distribution says which of three things it did.**
  `price_distribution(condition=, mileage_bucket=)` returns `basis.mode`:
  `filtered` when the slice itself clears `MIN_DISTRIBUTION_ADS`, `adjusted` when
  it does not and the pooled haircut is shifted onto the full scope, and
  `unconditioned` with `reason: no_measured_adjustment` when there is no measured
  haircut to shift by. The third case exists because the first draft claimed
  `adjusted` while applying a factor of 1.0 — an honest label on an answer that
  had not been adjusted at all. The `measured` flag is what separates them.
- **Budget is a quantised cache axis.** A toman amount is unbounded and a free
  axis is a fresh cache key per request behind an expensive scan (the same
  argument as `views._model_year`). `views.BUDGET_CACHE_GRID` rounds to 10M
  before keying and `MAX_BUDGET` caps it. `research.affordable()` groups
  `scorable_rows()` by cohort in **one** grouped aggregate — the first version
  called `cohort_peers` per cohort, which is the N+1 this codebase keeps
  re-learning — and ranks by `reach_pct` (what percentile of that cohort the
  budget buys) rather than by raw count.
- **Alert dedup is per user, not per ad.** `NotifiedAd` stays as the *operator*
  channel's once-ever guard; the per-user layer is `AlertDelivery`, unique on
  `(user, ad)`, because two people following the same car must both hear about
  it. It stores its own copy of `discount_pct` and `peer_median`: the feed has to
  keep saying what it said at the time, and the deal cache is rebuilt every hot
  tick. `MAX_PER_USER_PER_RUN` caps a run so one broad rule cannot bury an inbox.
  `notify.matching_deals()` is the one matcher — the operator settings and a
  user's `AlertRule` both go through it.
- **A scope key is a derived column because Postgres NULLs are distinct.** A
  unique constraint over `(user, brand_slug, model, variant, year_jalali)` does
  not stop a user following «all of Peugeot» twice, since `NULL != NULL` in a
  unique index. `ScopedToACar.save()` derives `scope_key` from whichever fields
  are set, narrowest last (`brand:peugeot/model:12/year:1401`), and the
  constraint is on that. The ordering also means a prefix match finds everything
  under a brand, and the UI's `scopeKey()` mirrors it so the client can tell
  whether a scope is already followed without a round trip.
- **Stale peers are not confident peers.** `pricing.tier()` drops one confidence
  tier when a cohort's newest `last_seen_at` is older than `COHORT_STALE_AFTER`
  (2 days), and the payload carries `cohort_stale`. Forty peers last seen three
  weeks ago used to read "high" exactly like forty fresh ones — the count was the
  only thing measured, and a cohort the crawler has stopped seeing is a cohort
  whose median describes a market that may have moved.
- **User-facing prose is composed in the UI, not the API.** Serializers return
  machine keys and facts (`reason`, `cohort_flags`, a component's `facts` dict);
  `ui.tsx:humanReason`, `FLAG_LABEL` and `Explorer.componentDetail` turn them
  into Persian. `fair_price` used to build its component `detail` as an English
  sentence, which shipped "median of 13 peers" into a Persian table on the one
  panel whose job is to make the number checkable.
- **A learned number never replaces a statistical one.** `peer_median` is still
  what the discount badge is measured against, on every board, including the
  `band=ml` one. `AdPrediction` sits *beside* it with its own decomposition
  attached, so a reader gets two independent accounts of the same car and can
  see where they disagree. `apps/core/pricing.py` opens with the record of what
  happened the last time a fitted model became the number on the card here
  (median r² 0.185, negative fair values, 148% "discounts"); that is the reason
  for the arrangement, not a general suspicion of models.
- **A model goes live by winning, not by being newest.** `registry.gate` refuses
  unless the challenger beats **both** the incumbent *and* the statistical
  baseline on the same time-split holdout, by `PROMOTION_MARGIN`. Beating only
  the incumbent is how a line of models drifts away from something simpler that
  was always better. The decision — challenger, incumbent, baseline, verdict —
  is written to `MLModel.metrics["promotion"]` either way and rendered on
  `/methodology`, so a model held in shadow is a published result rather than a
  silence. Rollback is one UPDATE, against a partial unique index that makes
  "which model is live" have exactly one answer per role.
- **Splits are by time, never at random.** A random split lets a model see July
  while predicting June, and the metric it then reports measures memory. The cut
  is on a *timestamp* rather than a row index, because dealer bulk uploads and
  reposts put the same car twice in one instant and an index cut would land the
  pair on opposite sides. Categorical vocabularies are fitted on the training
  half only — that leak is the one people miss.
- **A quantile model's band is conformalised before it is published.** Fitted
  quantiles are estimates of the training distribution's conditional quantiles
  and carry no allowance for the model's own out-of-sample error, so they are
  systematically too narrow: measured here at 43% coverage on a p10–p90 band
  while MAPE looked excellent. `train._conformal_delta` widens by the empirical
  quantile of the conformity score on a validation split, which buys a
  finite-sample marginal coverage guarantee with no distributional assumption.
  `inference` applies the same delta — serving the raw booster output would
  serve a band that is not the one whose coverage was measured. Coverage more
  than `COVERAGE_TOLERANCE_PP` off 80% **vetoes** promotion outright: accuracy
  cannot see a dishonest interval, so it cannot be the only gate.
- **The text classifier populates a queue; it never edits the catalogue.**
  `ingest._model` calls `get_or_create` on whatever string Bama sends, so there
  is no unmapped bucket to fill. The real problem it addresses is
  *fragmentation* — one car arriving under two spellings mints two `Model` rows
  and halves a cohort — which is what `ingest.BRAND_PARENT` fixes by hand at the
  brand level. Remapping a cohort key changes every price on the site, so it
  stays a staff review queue.
- **Drift is measured against the scoring population, not against "rows since
  training".** Ads published after the boundary are all young by construction,
  so `days_listed` spans a fortnight on that side against two months on the
  other and PSI read 8.0 on a feature that had not moved — pinning the verdict
  at "unstable" permanently, which is how a monitor gets ignored. Each side's
  features are also built against its own clock, or the passage of time itself
  reads as drift.
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
- **Complete means one page's worth of slack, not zero.** `coverage_is_complete`
  allows up to `COVERAGE_GAP_TOLERANCE_RANKS` (= one `PAGE_SIZE`) uncovered
  ranks across all gaps. Demanding a literal zero meant a single 403 mid-sweep
  suspended removal detection for the whole day, and the feed shifts by more
  than one page between reads anyway, so the strict reading was never achievable
  on a live feed. `uncovered_ranks(gaps)` is the arithmetic; the UI reports the
  measured number rather than a boolean, so the slack is visible.
- **A failed probe must not move the ratchet.** `_fetch_live(..., probe=True)`
  records a non-WAF failure as `SUCCEEDED` with `END_UNCONFIRMED` and no
  `feed_end_rank`, so a dead detail page cannot be mistaken for the end of the
  feed. A 403 still opens the cooldown breaker — that one is a block.
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
missing snapshot reports the crawler's downtime as a market move; and `alerts`
after `deal_scores`, because an alert run over an unrebuilt cache would mail out
yesterday's discounts as today's news; and `ml_score` after `deal_scores`, because
the prediction and the peer median are printed on one card and a reader has no
way to tell which half is stale. A step whose prerequisite failed is recorded
`skipped`, which is distinct from both success and silence.

Every step records a `JobRun` either way. Only the network fetch is retried.

`ml_train` is the exception to the "one worker runs everything" rule. It is the
only step that saturates a CPU for minutes rather than seconds, so it has its
own cadence (`train`), its own container (`ml` in compose, same image tag,
different command) and its own loop (`deploy/train.sh`) — inside `worker.sh` a
LightGBM fit would compete with the fetch tick for cores and show up as the
crawl mysteriously slowing down. It is deliberately absent from `full` for the
same reason: that is the command an operator runs to catch up, and it should not
take minutes.

The artifact volume is mounted **read-write by `ml` alone** and read-only
everywhere else. A web worker that can overwrite a model file can change what
every reader is told without a deploy, a migration, or a registry row.
