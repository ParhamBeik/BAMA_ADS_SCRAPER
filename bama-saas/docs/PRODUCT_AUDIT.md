# Product audit — can the site answer the four questions its customer arrives with?

Audited 2026-08-31 against `main` @ `10ca337`.

This repository is already evaluated as engineering, and it scores very well on
that axis: correct medians, honest coverage, refusal instead of a thin number.
It has never been evaluated as a *product* — whether a buyer or an investor who
arrives with a real question leaves with an answer.

Four questions define the customer:

1. **Where is the market going, overall and by segment, and should I buy or
   wait?** With the evidence for why those metrics were chosen.
2. **Is the data fresh, and what is the trend for the specific brand / model /
   trim I care about — am I paying high or low for it?**
3. **Alert me to genuinely good deals fast**, with the deal verified against
   mileage, damage, trim and the other things that actually move a price.
4. **Where do prices sit for a car I name — and, in reverse, what can I buy with
   the money I have?**

Backend and frontend are scored separately, because they fail differently. An
endpoint that computes the right answer and that no screen calls scores *for*
the backend and *against* the frontend; a panel that renders a number nobody can
act on scores the other way round.

---

## Scorecard

| # | Question | Backend | Frontend | The binding constraint |
| --- | --- | --- | --- | --- |
| 1 | Market direction & positioning | **7** | **6** | Index methodology is excellent; "which segment" only means brand or model, and nothing detects a turn |
| 2 | Freshness + per-scope trend | **6** | **6** | Coverage honesty is best-in-class; you cannot follow a scope over time |
| 3 | Verified good-deal alerts | **6** | **5** | Deal maths genuinely controls for mileage and damage; the alert half does not exist for users |
| 4 | Price distribution & budget fit | **6** | **4** | "Name a car → get the range" is strong; "I have X toman" has no entry point anywhere |
| | **Weighted** | **6.3** | **5.3** | The engine is better than the product built on it |

### The rubric

So the scores are reproducible rather than asserted. A score is the *lowest* band
whose conditions are all met.

| Band | Backend | Frontend |
| --- | --- | --- |
| **9–10** | The question is answered directly, conditioned on the variables that actually move the answer, with the sample and the uncertainty attached, and refusal where the data is too thin. | A reader who has never seen the app reaches the answer without composing it themselves from several panels. |
| **7–8** | The question is answered, and the method is sound and defensible, but one dimension the question named is missing or approximated. | Every input to the answer is on screen and correct; the reader still has to do the last step of synthesis. |
| **5–6** | Roughly half the question is answered well and the other half has no implementation at all, **or** the whole question is answered on a proxy (e.g. brand as a stand-in for segment). | The panels are correct and honest but the reader must already know what to ask; there is no path from the question to the screen. |
| **3–4** | The raw data supports the question but nothing computes it. | The data reaches the browser but no view is organised around the question. |
| **0–2** | The data is not collected. | Nothing on screen relates to the question. |

---

## What is already unusually good

Listed first, because these are load-bearing and remediation must not trade them
away. Most products at this stage have none of them.

- **One population, everywhere.** `pricing.scorable_rows()`
  (`apps/core/pricing.py:369`) is read by the deal board, `AdViewSet.get_queryset`
  (`views.py:340`), `_market_summary` (`views.py:422`), `model_search`
  (`views.py:238`) and `overview_view` (`views.py:1028`). Four screens finally
  report one catalogue total. This drifted twice before and the fix was
  centralisation, not a comment.
- **Refusal is a first-class answer.** `available: false` with a machine `reason`
  — `insufficient_peers`, `insufficient_clean_history`,
  `window_exceeds_clean_history`, `insufficient_years` — instead of a median of
  three cars. `research.turnover` (`research.py:596`) even distinguishes "this
  cohort will never have enough listings" from "this fills up on its own as
  history accrues", which are different facts for the reader.
- **Every figure carries its sample.** `movers` and `turnover` return the
  cohort/ad/episode counts behind each row and `Home.tsx` prints them as columns;
  `_series_sample` (`views.py:719`) additionally judges whether the sample is
  thin rather than leaving that to the reader; `DealCard` prints peer count and
  confidence dots beside every discount.
- **Thresholds are measured, not chosen.** `pricing.deal_window()`
  (`pricing.py:674`) derives both the recency window and the discount floor from
  the live board each rebuild; `condition_haircuts()` (`pricing.py:217`) and
  `mileage_haircuts()` (`pricing.py:299`) measure their ladders from the
  catalogue, and `_monotone` (`pricing.py:273`) forces severity to stay ordered so
  a sampling accident cannot make a repainted car worth more than a clean one.
- **Coverage is not decoration.** `views._coverage()` (`views.py:88`) reports
  `source_blocked` and `removal_detection_paused` — the same conditions the
  worker itself acts on — and `cached_answer` (`views.py:180`) stores the answer
  and its coverage as one entry so a badge can never describe a different vintage
  than the number it sits under.
- **The crawler's own downtime cannot read as a market move.**
  `research.compute_index` (`research.py:143`) withholds a return on a
  thinly-covered day *and does not advance `previous`*, so the next well-covered
  day chains back to the last well-covered one. This is subtle and it is right.

---

## Q1 — Market direction and positioning · Backend 7 / Frontend 6

> *"Is it going down generally and specifically for any parts of the market, or do
> we have a start of a price increase in a section of the market, so I know where
> to position myself — with concrete evidence for why those metrics were chosen."*

### Present

`research.compute_index` (`research.py:143`) is a matched-cohort chained index:
per-cohort returns weighted by the **smaller** of the two days' counts, so a
cohort cannot buy influence by ballooning overnight; winsorised at
`MAX_COHORT_RETURN`; and coverage-gated by `MIN_DAY_COVERAGE_RATIO`
(`research.py:95`). A cohort present on only one of two dates shifts the weights
and contributes no return, which is the property a raw median lacks and the
reason the headline number means anything at all.

`movers` (`research.py:315`), `arrivals` (`research.py:691`) and `turnover`
(`research.py:596`) supply the risers/fallers board and the supply-versus-demand
pair. `Home.tsx` renders all of it with `SeriesCaveats`, sparklines and the
sample columns.

### Gaps

**1. "Segment" only means brand or model.** `MarketIndex.Scope` is
market/brand/model (`apps/core/models.py:718`) and `jobs.market_index`
(`apps/jobs/jobs.py:437`) builds exactly those. There is no price band, no body
type, no model-year band, no domestic-versus-import axis. The question asks
whether *a part of the market* is turning; the app can only answer for a
manufacturer or a nameplate. `DailyInventorySnapshot` already carries what a
price-band or year-band scope would need, so this is a scope enum and a job loop,
not new data collection.

**2. Nothing detects a turn.** `research.py:356`:

```python
"change_pct": round((last[1] / first[1] - 1) * 100, 2),
```

This is a two-point chord across the window — no slope, no short-versus-long
divergence, no significance test — and it is the number that sorts the entire
movers board (`research.py:372`) and splits it into risers and fallers
(`research.py:384-387`). It is maximally sensitive to which day the window
happens to open on. *"Do we have the start of a price increase"* is precisely the
question a first-versus-last difference cannot answer: a scope that fell for
three weeks and has risen for four days reads as a faller.

**3. No synthesis into a position.** The home page presents index direction,
arrivals and turnover as three independent panels. Those three *are* the buy/wait
signal — rising index with falling supply and rising turnover is a different
market from a rising index with a supply glut — and the reader is left to combine
them with no guidance that they should.

**4. `methodology_version` points at nothing.** `views.py:73` sets it to `2`, the
envelope ships it on every answer, and `ui.tsx:210` renders it on every screen as
«روش محاسبه نسخه ۲». There is no document behind the number. The question
explicitly asks for the evidence behind the choices; that evidence exists, and it
is excellent, but it lives in code comments and `AGENTS.md` where no customer
will ever read it.

### Why 7 / 6

Backend is a 7 and not higher because one dimension the question named — segment
— is answered on a proxy, and the "starting to move" half is not implemented.
Frontend is a 6 because every input is on screen and correct, and the last step
of synthesis is left entirely to the reader.

---

## Q2 — Freshness and per-scope trend · Backend 6 / Frontend 6

> *"I want the freshness of the data validated, and updated news on any brand,
> model or variant I would like to purchase, with the trend for that
> model/variant specified, to see how high or low I am paying."*

### Present

The freshness half is the strongest thing in the codebase. `_coverage()`
(`views.py:88`) judges completeness on accumulated `PageCoverage` over the window
rather than on one run having reached the end — correct under a rolling crawl,
where no single run walks the feed end to end — tolerates
`COVERAGE_GAP_TOLERANCE_RANKS`, and reports `stale`, `age_hours`,
`source_blocked` and `removal_detection_paused`. `Provenance` (`ui.tsx:201`)
renders the two warnings *before* the counts, on the grounds that they change how
everything above should be read.

The trend half is well covered per scope. `Analyse.tsx` runs the scope from
market → brand → model → trim → model year in the URL, and `trendRequest`
(`Analyse.tsx:103`) routes trim/year to `/api/analytics/movement/` — computed per
request from the same snapshots — while coarser scopes read the persisted series.
`DistributionPanel` alongside it answers "am I paying high or low" at scope level,
and `PriceBar` answers it per listing.

### Gaps

**1. You cannot follow anything.** Watchlists and saved searches were removed in
`apps/accounts/migrations/0003_drop_watchlists_saved_searches.py`, and
`accounts/models.py` states the position outright: *"there is no subscription
tier, alerting, or in-app inbox."* `Favorite` is per-ad only. "Updated news on a
brand, model or variant I would like to purchase" has no storage, no delivery
mechanism and no screen. Everything about this question that involves *time* —
following a scope, being told when it moved — is architecturally absent, not
merely unbuilt.

**2. Freshness is global, never per-scope.** Coverage describes the whole crawl.
A model whose listings are all `UNVERIFIED`, or whose most recent snapshot is
three days old, gets the same strip as one refreshed ten minutes ago. Nothing
computes per-cohort staleness, and `pricing.tier()` (`pricing.py:77`) derives
confidence from peer *count* alone — a cohort of forty stale listings and a
cohort of forty fresh ones both read "high".

**3. One full provenance strip sits above four compact ones.** `ui.tsx:192`
documents this as deliberate, and the reasoning is sound — the same warning
repeated five times becomes wallpaper. But on `Home.tsx` the full strip is on the
index card (line 504) and the movers, arrivals, turnover and top-brands panels
use `compact` (lines 236, 283, 333, 548), which drops `source_blocked` and
`removal_detection_paused` entirely. On a long scroll a reader looking at a 4%
mover has no staleness warning in view. This is a judgment call rather than a
defect — `Analyse.tsx` uses the full strip on all four panels — but the trade-off
is worth re-examining, because the panels furthest from the full strip are the
ones whose numbers a block distorts most.

### Why 6 / 6

Both halves score 6 for the same reason: the freshness question is answered at a
9 and the "follow my car over time" question is answered at a 2, and the average
of an excellent implementation and an absent one is the middle of the band.

---

## Q3 — Verified good-deal alerts · Backend 6 / Frontend 5

> *"Notify me when genuinely good deals appear, as soon as possible, with the
> criteria verified — anomaly detection, or clustering ads for a variant into
> value tiers. Hard, because mileage, paint, PDR and damage all really move the
> price."*

### Present — and closer to the ask than the question assumes

The premise of the question is that conditioning a deal on mileage and damage is
the hard part and probably not done. It **is** done, and done carefully:

- Score is the plain gap to the (model, variant, `year_jalali`) peer median
  (`pricing.py:821`), deliberately measured against the median the card prints
  beside it so the arithmetic on screen reconciles.
- `MIN_PEERS = 8` (`pricing.py:53`) — below that it refuses to answer.
- Condition enters through four bands derived from Bama's structured
  `body_status`, which is populated on 100% of ads (`quality.py:138`,
  `quality.py:160`). The haircut is *measured* catalogue-wide, pooled, and
  sign-constrained by `_monotone`, after a version where a cohort's own band
  median let paint damage **raise** a car's value.
- Mileage enters through nine buckets with a measured fallback for thin buckets
  (`pricing.py:66`, `pricing.py:299`).
- `REVIEW_CONDITION_BANDS` (`pricing.py:643`) routes painted and structural cars
  to a review tab rather than the recommendation page.
- `MIN_ASK_VS_MEDIAN` (`pricing.py:609`) and `exclude_unclear_price`
  (`quality.py:207`) remove deposits and instalment listings — once 74% of the
  top 200 rows.
- `jobs.probe_sold` (`jobs.py:525`) checks served board rows against the detail
  page and removes anything Bama has already marked sold.
- Latency is good: the `hot` cadence (`pipeline.py:66`) runs fetch → mark_inactive
  → deal_scores → probe_sold → notify every 15 minutes.

`Deals.tsx` is the strongest screen in the app: three bands, freshness grouping
with headings, thresholds quoted from the API rather than described, and the
review band's rationale spelled out in Persian on the page.

### Gaps

**1. There is no alerting for users — the headline of the question.**
`NotifierSettings` is a singleton (`models.py:767`), `notifier_settings` is
`IsAdminUser` (`views.py:1064-1066`), and `NotifierPanel` in `Deals.tsx` is
gated on `user?.is_staff` (`Deals.tsx:151`). Delivery is Telegram only
(`notify.py:96`), to one chat id, for the whole site. For the target customer
this feature does not exist. Everything downstream — per-user criteria, an inbox,
"as soon as possible" — depends on a per-user layer that Q2 also needs.

**2. No multivariate model, no anomaly detection, no clustering.** The score is
univariate: one gap to one median, plus two additive corrections. There is no
residual model, no outlier detection beyond a per-cohort MAD threshold on the
high side (`pricing.py:534`), and no clustering of a model's listings into value
tiers.

The constraint any ML work must respect is recorded in `pricing.py:1-21`: an OLS
fit of price on mileage was **measured and rejected** — median r² 0.185, fitted on
as few as six points, producing negative adjusted prices and 148% "discounts".
That is an argument against *that* model on *that* feature set, not against all
models, but it sets the bar: a learned scorer needs a backtest, a calibration
check, and an explicit refusal threshold before it is allowed near the board.

**3. Signal columns are filterable but never scored.** `compute_deal_scores`
reads exactly nine columns (`pricing.py:746-749`): code, model, variant,
`year_jalali`, price, `first_seen_at`, mileage, `cohort_flags`, `body_status`.
Meanwhile `AdFilter` already exposes `seller_type`, `seller_authenticated`,
`city` and `condition` (`apps/core/filters.py`), and the schema also holds
`district`, `dealer`, `publish_at` tenure, and the full `PriceObservation` /
`PriceDropEvent` history. A car that has been listed 60 days with two price cuts
by a dealer in a distant city is a different proposition from a fresh private
listing at the same discount, and the score cannot tell them apart.

**4. "Profitable" is never answered.** `research.survival` (`research.py:526`)
and `turnover` (`research.py:596`) know how fast a model leaves the feed. Nothing
joins that to a deal card. A 15% discount on a car that sits for 90 days and 15%
on one that moves in 10 are presented identically, and the second is the one
worth interrupting someone for.

**5. Deal-board coverage is unmeasured.** `MIN_PEERS = 8` on a
(model, variant, `year_jalali`) key silently excludes an unknown share of the
catalogue — nobody knows whether the board can see 90% of listings or 40%. This
is one query against production and it should be a health check, not a mystery:

```sql
SELECT count(*) FILTER (WHERE d.ad_id IS NOT NULL)::float / count(*)
FROM catalog_ad a
LEFT JOIN analytics_dealscorecache d ON d.ad_id = a.code
WHERE a.status = 'active' AND a.price_basis_unclear = false;
```

### Why 6 / 5

Backend is a 6: the conditioning half of the question is answered at an 8, the
alerting and learned-detection half at a 2. Frontend is a 5: the board itself is
the best screen in the app, and the feature the question actually asked for is
invisible to every non-staff account.

---

## Q4 — Price distribution and budget fit · Backend 6 / Frontend 4

> *"Where do prices sit for a model or variant — and the reverse: I have this much
> money, what are my options, with some tolerance? Taking damage and mileage into
> account."*

### Present

`research.price_distribution` (`research.py:758`) answers the first half well:
percentiles, a square-root-rule histogram spanning p10–p90 with the tails
**counted rather than hidden**, city and model-year facets, and a `years` list
that rides along even on a refusal so a reader who picks a thin year is not
stranded with a disabled control. `pricing.peer_distribution` +
`PriceBar` answer "is *this specific car* cheap" on the listing page and in the
Explorer's fair-price sheet. `depreciation_curve` (`research.py:876`) covers value
by model year, reported as `pct_of_newest` rather than a compounded rate.

### Gaps

**1. Budget-first discovery does not exist, at either layer.** Nothing takes
"I have X toman" and returns *which models, trims and model years* are
attainable. `/api/ads/?price_min=&price_max=` returns individual listings, not
options. No screen starts from a budget; `PRICE_PRESETS` (`FilterPanel.tsx:52`)
narrows a list the reader has already chosen to browse.

The raw material exists and is **dead code**: `/api/markets/` (`views.py:459`,
backed by `_market_summary` at `views.py:422`) returns every model's ad count,
min, max and median price, ranked and cached — and no screen calls it. Verified:

```
$ grep -r "api/markets" ui/web/src     # no matches
```

It is covered by tests (`tests/test_api.py:411`) and reachable, so it has been
maintained for a consumer that was never built. This is the single largest
frontend gap in the audit and the cheapest to close.

**2. The distribution is not conditioned on the things the question named.**
`price_distribution` accepts brand, model, variant and year — and nothing about
the car. One histogram therefore mixes a 300,000 km repainted example with a
20,000 km clean one, and reports their combined spread as "what this model
costs". The question explicitly asks for the range *"keeping into consideration
the damage report and the mileage"*, and `pricing.condition_haircuts()` and
`mileage_haircuts()` already hold measured, monotone adjustments for exactly
this. The two modules do not talk to each other. Note the honest constraint:
slicing the distribution by band and bucket will drop many scopes below
`MIN_DISTRIBUTION_ADS` (`research.py:747`), so the useful form is probably an
*adjusted* distribution using the pooled haircuts rather than a filtered one.

**3. No tolerance band.** "Roughly this much, give or take" has no
representation. The filters are hard min/max, so a car 2% over budget is
invisible rather than shown and marked.

### Why 6 / 4

Backend is a 6: half the question is implemented well, the other half not at all,
and the implemented half ignores two variables the question named. Frontend is a
4 — the lowest score in the audit — because the data reaches the browser and no
view is organised around the question; the one endpoint that would serve it has
no caller.

---

## Ranked backlog

Ordered by questions unblocked ÷ cost. Each item names what would have to be
*measured* to prove it worked, because "it shipped" is not evidence.

| # | Work | Unblocks | Files | Proof it worked |
| --- | --- | --- | --- | --- |
| 1 | **Budget-first discovery.** A screen and endpoint that take a budget + tolerance and return attainable models/trims/years with their price shape. `/api/markets/` already computes most of it. | Q4 | `views.py:422`, new page under `ui/web/src/pages/` | A reader who knows only their budget reaches a shortlist in one screen; `/api/markets/` gains a caller |
| 2 | **Per-user watchlist + alert layer.** Follow a scope or a saved search; per-user criteria; an in-app feed, with Telegram as one channel rather than the only one. | Q2, Q3 | `apps/accounts/models.py`, `apps/core/notify.py`, new page | Alert precision measured over 30 days: what share of alerts the recipient opened, and how many good deals were missed |
| 3 | **Condition + mileage on the distribution.** Apply the existing pooled haircuts so the range shown is the range for *your* car. | Q4, Q2 | `research.py:758`, `pricing.py:217/299`, `Analyse.tsx` | Adjusted p25–p75 for a fixed (model, band, bucket) is materially narrower than the unadjusted band, without cohort loss |
| 4 | **Segment axes + a turn metric.** Price band / body type / year band on `MarketIndex.Scope`; replace the two-point chord with a slope plus a short-vs-long divergence and a significance bar. | Q1 | `models.py:718`, `jobs.py:437`, `research.py:315` | Backtest: does the turn metric flag a reversal earlier than `change_pct`, and what is its false-positive rate on the existing series? |
| 5 | **Liquidity on the deal card.** Join `turnover` / `survival` to the board so a discount carries how fast that car moves. | Q3, Q1 | `pricing.py:825`, `views.py:526`, `DealCard.tsx` | Board rows gain a time-to-sell figure with its own sample; review-band composition unchanged |
| 6 | **The ML layer**, scoped with its own honesty story: residual model over mileage/condition/trim/year/city/seller, anomaly detection, per-variant clustering into value tiers. Candidate generation first; the number on the card stays reconcilable until the model earns otherwise. | Q3 | new module under `apps/core/` | Backtest against realised delistings; calibration curve; a stated refusal threshold. It must beat `discount_pct` at predicting fast delisting, or it does not ship |
| 7 | **Methodology page behind `methodology_version`.** The evidence already exists in `AGENTS.md` and the module docstrings; it needs a customer-facing home and a link from `Provenance`. | Q1 | `views.py:73`, `ui.tsx:201`, new page | `methodology_version` becomes a link; the reasoning behind the index, the peer median and the 25% ceiling is reachable in one click from any number |
| 8 | **Per-scope freshness.** Per-cohort staleness feeding `confidence`, so a cohort of forty stale listings does not read "high". | Q2, Q3 | `pricing.py:77`, `views.py:88` | A scope whose snapshot is three days old is visibly distinguishable from one refreshed this hour |
| 9 | **Measure deal-board coverage** and make it a health check. | Q3 | `jobs.py:1104` | The share of active, clear-basis listings that carry a score is a number on the Control page |

### Non-blocking observations

- **`/api/analytics/deal-scores/<code>/`** (`views.py:712`) also has no caller.
  It exists so a detail card can never disagree with the row that was clicked;
  the Explorer instead calls `/api/ads/<code>/fair-price/`, which is a different
  number computed from a different path. Either wire it up or retire it — two
  routes to "what is this car worth" is how the last disagreement started.
- **The `compact` provenance trade-off** (Q2, gap 3) is worth a second look
  specifically on `Home.tsx`, where the full strip is four panels of scrolling
  away from the tables that a source block distorts most.

---

## How to check this document

Every claim above is meant to be verifiable without trusting the author:

- Every finding cites `file:line`; open the cited lines.
- "No screen calls X" was checked with `grep -r "<path>" ui/web/src` and the two
  positive results (`/api/markets/`, `/api/analytics/deal-scores/<code>/`) are
  named explicitly above.
- "No screen does X" was checked against all seven pages in `ui/web/src/pages/`
  plus `FilterPanel.tsx` and `DealCard.tsx`.
- Figures quoted from `AGENTS.md` and `README.md` — the 74% instalment
  contamination, the r² of 0.185, the 16.5% respray haircut — are reported **as
  the repository records them** and were not re-measured against production for
  this audit.
- No score was assigned without at least one cited line supporting it.

---

## Re-score after Phase 1 — 2026-09-01, branch `phase1-foundations`

Phase 1 of the plan is complete: the per-user layer, budget-first discovery,
conditioned distributions, segment axes and turn detection, liquidity on the
board, and per-cohort freshness. Scored against the *same rubric* above, on the
same read of "a score is the lowest band whose conditions are all met".

| # | Question | Backend | Frontend | What still holds it below 10 |
| --- | --- | --- | --- | --- |
| 1 | Market direction & positioning | 7 → **9** | 6 → **8** | The evidence for *why these metrics* is in the repo, not on a page the customer can open |
| 2 | Freshness + per-scope trend | 6 → **9** | 6 → **8** | You can follow a scope, but there is no one screen showing your followed scopes *with* their trends |
| 3 | Verified good-deal alerts | 6 → **8** | 5 → **8** | "Verified by a learned model" is Phase 2; today the verification is statistical |
| 4 | Price distribution & budget fit | 6 → **9** | 4 → **9** | A per-car prediction *interval* needs the quantile model |
| | **Weighted** | 6.3 → **8.8** | 5.3 → **8.3** | The product has caught up with the engine; the remaining gap is the learned layer and the published methodology |

### What moved each score

- **Q1 backend 7 → 9.** "Segment" stopped meaning "brand". `MarketIndex.Scope`
  carries `PRICE_BAND`, `YEAR_BAND` and `BODY_TYPE`, and membership is fixed from
  the latest snapshot so a band's series measures prices rather than
  reclassification. The two-point chord is no longer the only reading: every
  mover row carries a Theil–Sen slope, its sign agreement, a 7-day slope and a
  `turning` flag, and turns get their own ranked list. `market_read()` is the
  positioning answer the question actually asked, and it returns its three inputs
  so the reader can disagree with the synthesis. It refuses (`flow: unknown`)
  below `MIN_FLOW_EPISODES` rather than dividing two small numbers.
- **Q1 frontend 6 → 8.** `Home` opens with the market read; the movers table has
  segment tabs, a direction column and a separate turning section. Short of 9
  only because `methodology_version` is still printed on every screen with no
  document behind it.
- **Q2 backend 6 → 9.** Freshness is per cohort, not global: `COHORT_STALE_AFTER`
  drops a confidence tier and sets `cohort_stale`, so forty stale peers stop
  reading like forty fresh ones. `Watchlist` makes "the trim I care about" a
  persisted scope rather than a URL the reader has to keep.
- **Q2 frontend 6 → 8.** `FollowButton` puts following on the scope card and the
  listing page, and the header carries the unread badge. The last step of
  synthesis is still the reader's: there is no digest listing followed scopes
  beside their current trend.
- **Q3 backend 6 → 8.** The alert half now exists for users, not only the
  operator: `AlertRule` per user, `AlertDelivery` deduped per `(user, ad)` with
  its own copy of the discount so the feed keeps saying what it said, one shared
  matcher, and an `alerts` step in the hot cadence that depends on `deal_scores`.
  Liquidity rides on the deal row, so a discount now carries how fast that car
  moves. It stays in the 7–8 band on the rubric's own terms — the question named
  AI-verified criteria and the verification here is still statistical.
- **Q3 frontend 5 → 8.** An inbox, rule editing, an unread count visible from
  every screen, and `DealCard` printing the liquidity note. `ListingDetail` now
  calls `/api/analytics/deal-scores/<code>/`, which closes the first of the two
  non-blocking observations above — the endpoint has a caller and the detail
  card can no longer disagree with the row that was clicked.
- **Q4 backend 6 → 9.** `affordable()` answers the reverse question directly,
  grouped by cohort in one aggregate, ranked by what percentile of each cohort
  the budget reaches, refusing below `MIN_AFFORDABLE_ADS`. `price_distribution`
  takes a condition band and a mileage bucket and states which of three things it
  did (`filtered` / `adjusted` / `unconditioned`), so the range shown is the
  range for *your* car or it says why it is not.
- **Q4 frontend 4 → 9.** `/budget` is the entry point that did not exist: an
  amount, a tolerance, a ranked shortlist. `Analyse` gained the two conditioning
  controls and prints the basis note.

### Honest caveats on this re-score

- Scored by the author of both the rubric and the change, one day after the
  original audit. The rubric is reproducible — that is what it is for — but a
  second reader applying it is worth more than this table.
- Frontend scores were verified against a locally seeded database, not
  production. Every endpoint was exercised end to end (segment movers on all
  three new axes, the market read, the budget search, the conditioned
  distribution, watchlist idempotency, alert delivery and read-tracking).
- The two remaining ceilings are deliberate and sequenced, not overlooked: the
  learned layer is Phase 2 and the published methodology page ships with it,
  since its content is the model cards.

---

## Re-score after Phase 2 — the learned layer

Phase 2 shipped `apps/ml`: a quantile price model with conformalised intervals,
a calibrated time-to-sell classifier, an Isolation Forest, a text classifier
feeding a review queue, per-variant value tiers, a registry with a promotion
gate, drift monitoring, and a `/methodology` page generated from the registry.

| # | Question | Backend | Frontend | What still holds it below 10 |
| --- | --- | --- | --- | --- |
| 1 | Market direction & positioning | 9 → **10** | 8 → **9** | Frontend: the market read is one panel deep; a first-time reader still starts on a dashboard rather than on a sentence |
| 2 | Freshness + per-scope trend | 9 → **9** | 8 → **8** | Unchanged by this phase — the missing piece is a digest of *your* followed scopes with their trends |
| 3 | Verified good-deal alerts | 8 → **10** | 8 → **9** | Frontend: alert rules cannot yet be set on the model's residual, only on the cohort discount |
| 4 | Price distribution & budget fit | 9 → **10** | 9 → **10** | — |
| | **Weighted** | 8.8 → **9.8** | 8.3 → **9.0** | The backend answers all four questions with conditioning, uncertainty and refusal; the frontend has one real gap left |

### What moved

- **Q3 backend 8 → 10.** This is the question the phase was aimed at. "Verified
  using AI models, accounting for mileage, colour/damage, variant" now describes
  what actually runs: the price model reads seventeen features including the
  condition ordinal and mileage, the Isolation Forest separates *cheap* from
  *broken record* — two things the single MAD threshold reported identically —
  and `band=ml` is a board chosen and ranked by the model rather than by the
  cohort gap. Every estimate ships its exact TreeSHAP decomposition, and the
  refusal path is real: no active model means null columns and an
  `available: false`, not a zero.
- **Q1 backend 9 → 10** and **Q4 backend 9 → 10.** Both were held below 10 by
  the same missing thing — "with the evidence for why those metrics were
  chosen" (Q1) and a genuine per-car prediction interval (Q4).
  `/api/ml/models/` and `/methodology` supply the first; the conformalised
  p10–p90 supplies the second, and it is an interval with a measured coverage
  guarantee rather than a range drawn around a median.
- **Q1/Q3/Q4 frontend +1 each.** The model cards, the `ml` board with its own
  explanation, the estimate panel on the listing page, and a
  `methodology_version` badge that finally links somewhere.

### What did not move, and why

**Q2 is unchanged at 9/8.** Nothing in this phase addressed it, and saying so
is more useful than finding a way to credit it. The gap is the same one the
Phase 1 re-score named: a reader can follow a scope and can see any scope's
trend, but there is no one screen showing *their* followed scopes beside their
current trends. That is a page, not a model.

### The honest part

Two of the five models were held in **shadow** on the first run against a
realistic catalogue, and both refusals were correct:

- The price model was vetoed with `interval_coverage_off_target` when its
  p10–p90 contained 43% of held-out cars against a target of 80% — while its
  MAPE was 6.4% against the peer median's 10.5%. It would have passed an
  accuracy-only gate. Adding early stopping moved coverage to 58%; conformal
  calibration moved it to 84%, which is when it was promoted.
- The Isolation Forest scored a lift of 0.85 — its flagged listings left the
  feed *less* often than average. The first version of the gate promoted it
  anyway, because it was passed `baseline=None` and `gate` reads "nothing to
  beat" as "beat it". Lift has a baseline built into its definition: 1.0 is
  random. That is now the number it has to clear.

Both are in the test suite as named cases. A gate that has never refused
anything is not known to work.

### Caveats that still stand

- Scored by the author of the rubric and the change. Reproducible, but a second
  reader is worth more.
- The metrics quoted above were measured on a **synthetic catalogue** built to
  have a genuine price signal, not on production. The pipeline, the gate and the
  refusals are verified end to end; the specific numbers a production refit
  produces will differ, and the `/methodology` page is where they will be read.
- Lighthouse 100 (Phase 3) is untouched. The main bundle is 421KB and
  `Chart.tsx` is still 1.1MB.
