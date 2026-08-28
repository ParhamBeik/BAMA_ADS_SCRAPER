"""Matched-cohort market index.

Unit tests: the index is pure arithmetic over ``DailyInventorySnapshot`` rows —
no network, no HTTP, no derived-service chain — so the cheapest test that can
fail when the logic breaks constructs those rows directly and reads the series
back. The one integration-level case (the API endpoint) is at the bottom, since
serialization and the scope/param contract cross a real boundary.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import (
    Ad,
    Brand,
    DailyInventorySnapshot,
    ListingEpisode,
    MarketIndex,
    Model,
    Variant,
)
from apps.core.research import (
    BASE_VALUE,
    MIN_COHORT_ADS,
    MIN_DISTRIBUTION_ADS,
    MOVER_MIN_DAYS,
    TURNOVER_MIN_EPISODES,
    arrivals,
    build_index,
    cohort_series,
    compute_index,
    movers,
    price_distribution,
    turnover,
)

D0 = date(2026, 8, 1)
D1 = D0 + timedelta(days=1)


@pytest.fixture
def cohorts(db):
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو")
    model = Model.objects.create(brand=brand, name_fa="پژو ۲۰۶")
    cheap = Variant.objects.create(model=model, name_fa="تیپ ۱")
    dear = Variant.objects.create(model=model, name_fa="تیپ ۵")
    return {"brand": brand, "model": model, "cheap": cheap, "dear": dear}


def _snap(model, variant, on, *, price, count, year=1399):
    return DailyInventorySnapshot.objects.create(
        model=model, variant=variant, year_jalali=year, date=on,
        ad_count=count, new_count=0, median_price=price,
        mean_price=price, min_price=price, max_price=price,
    )


# ---------------------------------------------------------------------------
# The property the whole feature exists for
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_index_ignores_composition_shift(cohorts):
    """A flood of cheap listings must move the median but NOT the index.

    Both cohorts hold their price exactly. Only the *mix* changes: the cheap
    cohort triples in size. A naive median over all listings falls hard; the
    index, which only ever compares a cohort against itself, must stay flat.
    """
    model, cheap, dear = cohorts["model"], cohorts["cheap"], cohorts["dear"]

    # Day 0: 10 cheap cars at 500M, 10 expensive at 1500M.
    _snap(model, cheap, D0, price=500_000_000, count=10)
    _snap(model, dear, D0, price=1_500_000_000, count=10)
    # Day 1: identical prices, but 30 cheap cars now vs the same 10 expensive.
    _snap(model, cheap, D1, price=500_000_000, count=30)
    _snap(model, dear, D1, price=1_500_000_000, count=10)

    series = compute_index(cohort_series(MarketIndex.Scope.MARKET))
    assert len(series) == 2
    assert series[0]["index_value"] == BASE_VALUE
    # No cohort changed price, so the market did not move.
    assert series[1]["return_pct"] == pytest.approx(0.0, abs=1e-6)
    assert series[1]["index_value"] == pytest.approx(BASE_VALUE, abs=1e-6)

    # ...while the ad-count-weighted median across all listings drops sharply,
    # which is exactly the misleading number the index replaces.
    def naive_median(rows):
        prices = []
        for price, count in rows:
            prices.extend([price] * count)
        return statistics.median(prices)

    before = naive_median([(500_000_000, 10), (1_500_000_000, 10)])
    after = naive_median([(500_000_000, 30), (1_500_000_000, 10)])
    assert after < before


@pytest.mark.django_db
def test_index_tracks_genuine_price_move(cohorts):
    """When cohorts actually reprice, the index must follow — size-weighted."""
    model, cheap, dear = cohorts["model"], cohorts["cheap"], cohorts["dear"]

    _snap(model, cheap, D0, price=500_000_000, count=30)
    _snap(model, dear, D0, price=1_000_000_000, count=10)
    # Cheap cohort +10%, expensive flat. Weights 30 vs 10 → +7.5% overall.
    _snap(model, cheap, D1, price=550_000_000, count=30)
    _snap(model, dear, D1, price=1_000_000_000, count=10)

    series = compute_index(cohort_series(MarketIndex.Scope.MARKET))
    assert series[1]["return_pct"] == pytest.approx(7.5, abs=1e-6)
    assert series[1]["index_value"] == pytest.approx(107.5, abs=1e-6)
    assert series[1]["cohort_count"] == 2


@pytest.mark.django_db
def test_index_skips_cohorts_below_min_ads(cohorts):
    """A cohort too thin to have a meaningful median contributes no return."""
    model, cheap, dear = cohorts["model"], cohorts["cheap"], cohorts["dear"]

    thin = MIN_COHORT_ADS - 1
    _snap(model, cheap, D0, price=500_000_000, count=thin)
    _snap(model, dear, D0, price=1_000_000_000, count=10)
    # The thin cohort halves in price; it must be ignored entirely.
    _snap(model, cheap, D1, price=250_000_000, count=thin)
    _snap(model, dear, D1, price=1_000_000_000, count=10)

    series = compute_index(cohort_series(MarketIndex.Scope.MARKET))
    assert series[1]["cohort_count"] == 1  # only the fat cohort counted
    assert series[1]["return_pct"] == pytest.approx(0.0, abs=1e-6)


@pytest.mark.django_db
def test_index_clips_absurd_cohort_moves(cohorts):
    """A single cohort cannot drag the headline with an implausible jump."""
    model, cheap = cohorts["model"], cohorts["cheap"]

    _snap(model, cheap, D0, price=100_000_000, count=10)
    _snap(model, cheap, D1, price=1_000_000_000, count=10)  # 10x overnight

    series = compute_index(cohort_series(MarketIndex.Scope.MARKET))
    # Clipped to MAX_COHORT_RETURN (+50%), not +900%.
    assert series[1]["return_pct"] == pytest.approx(50.0, abs=1e-6)


@pytest.mark.django_db
def test_new_cohort_contributes_no_return(cohorts):
    """A cohort that did not exist yesterday changes weights, never the index."""
    model, cheap, dear = cohorts["model"], cohorts["cheap"], cohorts["dear"]

    _snap(model, cheap, D0, price=500_000_000, count=10)
    _snap(model, cheap, D1, price=500_000_000, count=10)
    # Brand-new cohort, wildly different price level, appears on day 1 only.
    _snap(model, dear, D1, price=9_000_000_000, count=50)

    series = compute_index(cohort_series(MarketIndex.Scope.MARKET))
    assert series[1]["cohort_count"] == 1
    assert series[1]["return_pct"] == pytest.approx(0.0, abs=1e-6)


@pytest.mark.django_db
def test_index_carries_level_when_nothing_matches(cohorts):
    """No cohort on both dates → hold the level, do not invent a move."""
    model, cheap, dear = cohorts["model"], cohorts["cheap"], cohorts["dear"]

    _snap(model, cheap, D0, price=500_000_000, count=10)
    _snap(model, dear, D1, price=900_000_000, count=10)

    series = compute_index(cohort_series(MarketIndex.Scope.MARKET))
    assert series[1]["return_pct"] is None
    assert series[1]["index_value"] == pytest.approx(BASE_VALUE)


@pytest.mark.django_db
def test_build_index_is_idempotent(cohorts):
    """Rebuilds replace, never accumulate — the series is chained, so a stale
    suffix would corrupt every later value."""
    model, cheap = cohorts["model"], cohorts["cheap"]
    _snap(model, cheap, D0, price=500_000_000, count=10)
    _snap(model, cheap, D1, price=550_000_000, count=10)

    first = build_index(MarketIndex.Scope.MARKET, None)
    second = build_index(MarketIndex.Scope.MARKET, None)
    assert first == second == 2
    assert MarketIndex.objects.filter(scope=MarketIndex.Scope.MARKET).count() == 2


@pytest.mark.django_db
def test_brand_scope_isolates_its_own_cohorts(cohorts):
    """A brand series must not be moved by another brand's prices."""
    model, cheap = cohorts["model"], cohorts["cheap"]
    other_brand = Brand.objects.create(slug="pride", name_fa="پراید")
    other_model = Model.objects.create(brand=other_brand, name_fa="پراید ۱۳۱")
    other_variant = Variant.objects.create(model=other_model, name_fa="ساده")

    _snap(model, cheap, D0, price=500_000_000, count=10)
    _snap(model, cheap, D1, price=500_000_000, count=10)      # peugeot flat
    _snap(other_model, other_variant, D0, price=200_000_000, count=10)
    _snap(other_model, other_variant, D1, price=240_000_000, count=10)  # +20%

    build_index(MarketIndex.Scope.BRAND, "peugeot")
    build_index(MarketIndex.Scope.BRAND, "pride")

    peugeot = MarketIndex.objects.get(
        scope=MarketIndex.Scope.BRAND, scope_id="peugeot", date=D1
    )
    pride = MarketIndex.objects.get(
        scope=MarketIndex.Scope.BRAND, scope_id="pride", date=D1
    )
    assert peugeot.return_pct == pytest.approx(0.0, abs=1e-6)
    assert pride.return_pct == pytest.approx(20.0, abs=1e-6)


@pytest.mark.django_db
def test_market_index_endpoint(cohorts):
    """Integration: the endpoint's scope contract and change_pct summary."""
    model, cheap = cohorts["model"], cohorts["cheap"]
    _snap(model, cheap, D0, price=500_000_000, count=10)
    _snap(model, cheap, D1, price=550_000_000, count=10)
    build_index(MarketIndex.Scope.MARKET, None)

    client = APIClient()
    url = reverse("core:market-index")

    res = client.get(url)
    assert res.status_code == 200
    body = res.json()
    assert body["scope"] == "market"
    assert len(body["series"]) == 2
    assert body["latest_index"] == pytest.approx(110.0, abs=1e-6)
    assert body["change_pct"] == pytest.approx(10.0, abs=1e-6)
    assert body["window"]["requested_days"] == 90
    assert body["window"]["days"] == 2
    assert body["window"]["clamped"] is True

    # A non-market scope without ?id is a client error, not an empty series.
    assert client.get(url, {"scope": "brand"}).status_code == 400
    assert client.get(url, {"scope": "nonsense"}).status_code == 400


# ---------------------------------------------------------------------------
# Narrowing below the persisted scopes
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_cohort_series_narrows_to_one_trim_and_year(cohorts):
    """Unit: the filter that makes a trim-level index possible at all.

    Pure queryset arithmetic over snapshot rows, so the cheapest test that can
    fail is one that writes those rows and reads the grouping back.

    Two trims and two model years of the same model exist. Asking for one trim
    must leave one cohort per date, and asking for the wrong year must leave
    none — an empty series, not a series quietly built from the other year.
    """
    model, cheap, dear = cohorts["model"], cohorts["cheap"], cohorts["dear"]
    _snap(model, cheap, D0, price=500_000_000, count=10, year=1399)
    _snap(model, dear, D0, price=900_000_000, count=10, year=1399)
    _snap(model, cheap, D0, price=600_000_000, count=10, year=1400)

    everything = cohort_series(MarketIndex.Scope.MODEL, str(model.pk))
    assert len(everything[D0]) == 3

    one_trim = cohort_series(MarketIndex.Scope.MODEL, str(model.pk), variant_id=cheap.pk)
    assert len(one_trim[D0]) == 2  # same trim, two model years

    one_cohort = cohort_series(
        MarketIndex.Scope.MODEL, str(model.pk), variant_id=cheap.pk, year_jalali=1400,
    )
    assert list(one_cohort[D0].values()) == [(600_000_000, 10)]

    assert cohort_series(
        MarketIndex.Scope.MODEL, str(model.pk), variant_id=dear.pk, year_jalali=1400,
    ) == {}


# ---------------------------------------------------------------------------
# Movers
# ---------------------------------------------------------------------------

def _index_row(scope, scope_id, on, value, *, cohorts_n=8, ads=40):
    return MarketIndex.objects.create(
        scope=scope, scope_id=scope_id, date=on, index_value=value,
        return_pct=None, cohort_count=cohorts_n, ad_count=ads,
    )


@pytest.mark.django_db
def test_movers_ranks_by_change_and_splits_on_sign(db):
    """Unit: ranking is over stored index rows, so this writes them directly.

    Three models: one up 20%, one down 10%, one flat. A model that rose must
    never appear among the fallers — slicing the two ends of a sorted list would
    put the flat one there, and with fewer scopes than the limit it would put
    the *riser* there too.
    """
    today = timezone.now().date()
    for offset in range(5):
        on = today - timedelta(days=4 - offset)
        _index_row(MarketIndex.Scope.MODEL, "1", on, 100.0 + offset * 5)   # +20%
        _index_row(MarketIndex.Scope.MODEL, "2", on, 100.0 - offset * 2.5)  # -10%
        _index_row(MarketIndex.Scope.MODEL, "3", on, 100.0)                 # flat

    result = movers(MarketIndex.Scope.MODEL, days=30, limit=10)
    assert result["available"] is True
    assert result["scopes_ranked"] == 3

    assert [r["scope_id"] for r in result["risers"]] == ["1"]
    assert result["risers"][0]["change_pct"] == pytest.approx(20.0)
    assert [r["scope_id"] for r in result["fallers"]] == ["2"]
    assert result["fallers"][0]["change_pct"] == pytest.approx(-10.0)

    # The sample behind the move rides along, because a move off three cohorts
    # and a move off forty are not the same claim.
    assert result["risers"][0]["cohort_count"] == 8
    assert result["risers"][0]["ad_count"] == 40
    assert result["risers"][0]["series"] == [100.0, 105.0, 110.0, 115.0, 120.0]


@pytest.mark.django_db
def test_movers_refuses_a_scope_with_too_little_history(db):
    """Two dates is a gap between two days, not a trend."""
    today = timezone.now().date()
    for offset in range(MOVER_MIN_DAYS - 1):
        _index_row(MarketIndex.Scope.MODEL, "1", today - timedelta(days=offset), 100.0 + offset)

    assert movers(MarketIndex.Scope.MODEL, days=30)["available"] is False


@pytest.mark.django_db
def test_movers_ignores_dates_outside_the_window(db):
    """The window is applied to the stored dates, not assumed to be the series.

    A scope whose series runs far back must be measured across the requested
    window only — otherwise every "30 day" figure silently reports all of
    history, which is the same bug the market-index window clamp exists for.
    """
    today = timezone.now().date()
    for offset in range(40):
        on = today - timedelta(days=39 - offset)
        # Doubles over the first ten days, then holds perfectly flat.
        _index_row(MarketIndex.Scope.MODEL, "1", on, 100.0 + min(offset, 10) * 10)

    windowed = movers(MarketIndex.Scope.MODEL, days=7)
    assert windowed["available"] is True
    # Nothing moved inside the last seven days, so there is no riser to report.
    assert windowed["risers"] == []
    assert windowed["scopes_ranked"] == 1

    lifetime = movers(MarketIndex.Scope.MODEL, days=365)
    assert lifetime["risers"][0]["change_pct"] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Turnover: how fast a model's listings leave the feed
# ---------------------------------------------------------------------------

def _episode(ad, *, started, ended=None):
    return ListingEpisode.objects.create(ad=ad, started_at=started, ended_at=ended)


def _ad(model, code, *, brand):
    return Ad.objects.create(
        code=code, brand=brand, model=model, title=code,
        year_jalali=1399, current_price=1_000_000_000,
        publish_at=timezone.now(), last_seen_at=timezone.now(),
    )


@pytest.fixture
def episode_model(cohorts, settings):
    """A model plus a clean-start cut old enough not to be the thing under test.

    `BAMA_EPISODE_CLEAN_START` is a real production date (episodes before it
    measured the sweep schedule rather than the market). Left at its real value
    these tests would pass or fail depending on how long ago that date was,
    which is a calendar test, not a turnover one.
    """
    settings.BAMA_EPISODE_CLEAN_START = "2020-01-01"
    return cohorts["model"], cohorts["brand"]


@pytest.mark.django_db
def test_turnover_only_counts_listings_that_had_the_full_window(episode_model):
    """Unit: the rule that makes this number comparable at all.

    Twenty listings started 60 days ago and half of them left inside 30 days, so
    the rate is 50%. Twenty more started *yesterday* and none have left. Counting
    those would halve the rate by adding cars that have not had a chance to go —
    the same one-directional error a mean over finished listings makes, from the
    other end.
    """
    model, brand = episode_model
    now = timezone.now()
    for i in range(20):
        ad = _ad(model, f"old{i}", brand=brand)
        _episode(ad, started=now - timedelta(days=60),
                 ended=now - timedelta(days=40) if i < 10 else None)
    for i in range(20):
        _episode(_ad(model, f"new{i}", brand=brand), started=now - timedelta(days=1))

    result = turnover(days=30)
    assert result["available"] is True
    row = result["fastest"][0]
    assert row["n"] == 20, "the twenty listings started yesterday are not eligible yet"
    assert row["left_within_window"] == 10
    assert row["left_pct"] == pytest.approx(50.0)


@pytest.mark.django_db
def test_turnover_excludes_departures_after_the_window(episode_model):
    """A listing that took 45 days to go did not leave within 30."""
    model, brand = episode_model
    now = timezone.now()
    for i in range(20):
        ad = _ad(model, f"slow{i}", brand=brand)
        _episode(ad, started=now - timedelta(days=90), ended=now - timedelta(days=45))

    assert turnover(days=30)["fastest"][0]["left_pct"] == pytest.approx(0.0)
    # The same listings, measured over a window they do fit inside.
    assert turnover(days=60)["fastest"][0]["left_pct"] == pytest.approx(100.0)


@pytest.mark.django_db
def test_turnover_is_unavailable_until_the_clean_window_is_long_enough(
    cohorts, settings,
):
    """The window has to fit inside the trustworthy history, or there is nothing
    to measure.

    Removal dates before the clean-start cut record when the crawler managed a
    full sweep, not when cars left, so they are excluded. Until that cut is more
    than `days` old, no listing can have completed the window — and the honest
    answer is "not yet", not a rate computed from whatever survived.
    """
    model, brand = cohorts["model"], cohorts["brand"]
    now = timezone.now()
    settings.BAMA_EPISODE_CLEAN_START = (now - timedelta(days=10)).date().isoformat()
    for i in range(30):
        _episode(_ad(model, f"recent{i}", brand=brand), started=now - timedelta(days=5))

    result = turnover(days=30)
    assert result["available"] is False
    # Distinct from "too few listings": this one says the window is the problem,
    # so the screen can suggest a shorter one instead of implying no data.
    assert result["reason"] == "window_exceeds_clean_history"
    assert result["clean_days"] == 10

    # The same listings, over a window that does fit inside clean history.
    assert turnover(days=3)["available"] is True


@pytest.mark.django_db
def test_turnover_refuses_a_model_with_too_few_episodes(episode_model):
    model, brand = episode_model
    now = timezone.now()
    for i in range(TURNOVER_MIN_EPISODES - 1):
        _episode(_ad(model, f"thin{i}", brand=brand), started=now - timedelta(days=60))

    assert turnover(days=30)["available"] is False


# ---------------------------------------------------------------------------
# Price distribution
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_price_distribution_reports_percentiles_and_bounds_the_histogram(cohorts):
    """Unit: percentiles and bucketing over rows written directly.

    Twenty cars evenly spread, plus one typo listing at 5.8 trillion. The typo
    must show up in `max` and be counted above the band — and must not be
    allowed to define the histogram's range, or every real car lands in bucket
    one and the chart says nothing.
    """
    model, brand = cohorts["model"], cohorts["brand"]
    for i in range(20):
        Ad.objects.create(
            code=f"d{i}", brand=brand, model=model, title=f"d{i}",
            year_jalali=1399, current_price=1_000_000_000 + i * 50_000_000,
            publish_at=timezone.now(), last_seen_at=timezone.now(),
        )
    Ad.objects.create(
        code="typo", brand=brand, model=model, title="typo", year_jalali=1399,
        current_price=5_800_000_000_000,
        publish_at=timezone.now(), last_seen_at=timezone.now(),
    )

    result = price_distribution(model_id=model.pk)
    assert result["available"] is True
    assert result["distribution"]["count"] == 21
    assert result["distribution"]["max"] == 5_800_000_000_000
    # p90 is a real car, not the typo.
    assert result["distribution"]["p90"] < 2_000_000_000

    histogram = result["histogram"]
    assert histogram["to"] == result["distribution"]["p90"]
    assert histogram["above"] >= 1, "the typo is counted outside the band, not hidden"
    assert sum(b["n"] for b in histogram["buckets"]) + histogram["above"] \
        + histogram["below"] == 21
    # A real spread, not everything crushed into one bar.
    assert sum(1 for b in histogram["buckets"] if b["n"]) > 1


@pytest.mark.django_db
def test_price_distribution_drops_installment_ads(cohorts):
    """A down payment is not a car's price, and would fake a cheap cluster."""
    model, brand = cohorts["model"], cohorts["brand"]
    for i in range(20):
        Ad.objects.create(
            code=f"cash{i}", brand=brand, model=model, title=f"cash{i}",
            year_jalali=1399, current_price=1_000_000_000 + i * 10_000_000,
            publish_at=timezone.now(), last_seen_at=timezone.now(),
        )
    for i in range(10):
        Ad.objects.create(
            code=f"credit{i}", brand=brand, model=model, title=f"credit{i}",
            year_jalali=1399, current_price=200_000_000,
            price_type="installment",
            publish_at=timezone.now(), last_seen_at=timezone.now(),
        )

    result = price_distribution(model_id=model.pk)
    assert result["distribution"]["count"] == 20
    assert result["distribution"]["min"] >= 1_000_000_000


@pytest.mark.django_db
def test_price_distribution_refuses_a_scope_that_is_too_thin(cohorts):
    model, brand = cohorts["model"], cohorts["brand"]
    for i in range(MIN_DISTRIBUTION_ADS - 1):
        Ad.objects.create(
            code=f"few{i}", brand=brand, model=model, title=f"few{i}",
            year_jalali=1399, current_price=1_000_000_000,
            publish_at=timezone.now(), last_seen_at=timezone.now(),
        )

    result = price_distribution(model_id=model.pk)
    assert result["available"] is False
    assert result["reason"] == "insufficient_listings"


@pytest.mark.django_db
def test_year_options_ignore_the_selected_year(cohorts):
    """Unit: the year facet describes the scope, not the year already chosen.

    It is also the option list the picker renders. Computed after the year
    filter, a scope narrowed to 1399 offers 1399 as its only choice — you could
    pick a model year but never switch to a different one without clearing back
    to "all years" first.
    """
    model, brand = cohorts["model"], cohorts["brand"]
    for year in (1399, 1400, 1401):
        for i in range(MIN_DISTRIBUTION_ADS):
            Ad.objects.create(
                code=f"y{year}n{i}", brand=brand, model=model, title=f"y{year}",
                year_jalali=year, current_price=1_000_000_000 + year * 1_000,
                publish_at=timezone.now(), last_seen_at=timezone.now(),
            )

    everything = price_distribution(model_id=model.pk)
    assert [y["year_jalali"] for y in everything["years"]] == [1399, 1400, 1401]

    one_year = price_distribution(model_id=model.pk, year_jalali=1400)
    # The prices narrow to the chosen year...
    assert one_year["distribution"]["count"] == MIN_DISTRIBUTION_ADS
    # ...but every year stays reachable in one click.
    assert [y["year_jalali"] for y in one_year["years"]] == [1399, 1400, 1401]


@pytest.mark.django_db
def test_histogram_last_bucket_covers_the_top_of_the_band(cohorts):
    """The clamp folds the remainder of an integer-divided range into the last
    bucket, so that bucket has to say it reaches the top of the band."""
    model, brand = cohorts["model"], cohorts["brand"]
    for i in range(40):
        Ad.objects.create(
            code=f"h{i}", brand=brand, model=model, title=f"h{i}",
            year_jalali=1399, current_price=1_000_000_000 + i * 7_777_777,
            publish_at=timezone.now(), last_seen_at=timezone.now(),
        )

    result = price_distribution(model_id=model.pk)
    histogram, p90 = result["histogram"], result["distribution"]["p90"]
    assert histogram["buckets"][-1]["to"] == p90
    # Nothing inside the band goes uncounted.
    inside = result["distribution"]["count"] - histogram["below"] - histogram["above"]
    assert sum(b["n"] for b in histogram["buckets"]) == inside


@pytest.mark.django_db
@pytest.mark.parametrize("n_ads,expected", [(16, 6), (100, 10), (900, 24)])
def test_histogram_bars_follow_the_sample_size(cohorts, n_ads, expected):
    """Bar count scales with sqrt(n), floored at 6 and capped at 24.

    Twenty-four fixed bars over a 25-ad model year draws mostly bars of height
    one, which a reader interprets as structure when it is the sample's noise.
    """
    model, brand = cohorts["model"], cohorts["brand"]
    Ad.objects.bulk_create([
        Ad(
            code=f"n{i}", brand=brand, model=model, title=f"n{i}",
            year_jalali=1399, current_price=1_000_000_000 + i * 5_000_000,
            publish_at=timezone.now(), last_seen_at=timezone.now(),
        )
        for i in range(n_ads)
    ])
    result = price_distribution(model_id=model.pk)
    histogram = result["histogram"]
    assert len(histogram["buckets"]) == expected
    # Whatever the count, the band is fully covered and fully accounted for.
    assert histogram["buckets"][0]["from"] == histogram["from"]
    assert histogram["buckets"][-1]["to"] == histogram["to"]
    inside = result["distribution"]["count"] - histogram["below"] - histogram["above"]
    assert sum(b["n"] for b in histogram["buckets"]) == inside


# ---------------------------------------------------------------------------
# Arrivals — the supply half of the home page
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_arrivals_sums_new_but_never_sums_inventory(cohorts):
    """Unit: new listings accumulate over the window; standing stock does not.

    ``new_count`` is an event — cars first seen that day — so it adds up. But
    ``ad_count`` is a level, and summing it over the window would count the same
    unsold car once per day it sat there, reporting a model with 20 stale
    listings as though 600 cars had passed through it.
    """
    model, cheap = cohorts["model"], cohorts["cheap"]
    today = timezone.now().date()
    for offset in range(5):
        DailyInventorySnapshot.objects.create(
            model=model, variant=cheap, year_jalali=1399,
            date=today - timedelta(days=offset),
            ad_count=20, new_count=3, median_price=500_000_000,
            mean_price=500_000_000, min_price=500_000_000, max_price=500_000_000,
        )

    result = arrivals(days=30)
    assert result["available"] is True
    row = result["models"][0]
    assert row["new_listings"] == 15          # 3 a day for five days
    assert row["listed_now"] == 20            # the latest level, not 100


@pytest.mark.django_db
def test_arrivals_refuses_a_window_where_nothing_arrived(cohorts):
    """Snapshots exist but no car is new: a board of zeroes is not a board."""
    model, cheap = cohorts["model"], cohorts["cheap"]
    DailyInventorySnapshot.objects.create(
        model=model, variant=cheap, year_jalali=1399, date=timezone.now().date(),
        ad_count=20, new_count=0, median_price=500_000_000,
        mean_price=500_000_000, min_price=500_000_000, max_price=500_000_000,
    )
    assert arrivals(days=30)["available"] is False


@pytest.mark.django_db
def test_arrivals_is_empty_before_any_snapshot_exists():
    assert arrivals(days=30)["available"] is False


# ---------------------------------------------------------------------------
# Histogram edges
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_histogram_survives_a_band_of_zero_width(cohorts):
    """Every listing asking the same price is routine for a factory-priced trim.

    p10 and p90 then coincide, and bars laid out at a floored width of 1 would
    run off the top of a band with no width at all — the last of them getting
    its top edge pulled back below its own start, a bar that reads backwards.
    """
    model, brand = cohorts["model"], cohorts["brand"]
    Ad.objects.bulk_create([
        Ad(
            code=f"flat{i}", brand=brand, model=model, title=f"flat{i}",
            year_jalali=1399, current_price=1_000_000_000,
            publish_at=timezone.now(), last_seen_at=timezone.now(),
        )
        for i in range(MIN_DISTRIBUTION_ADS)
    ])

    histogram = price_distribution(model_id=model.pk)["histogram"]
    assert all(b["to"] >= b["from"] for b in histogram["buckets"])
    assert all(histogram["from"] <= b["from"] <= histogram["to"] for b in histogram["buckets"])
    # Every car is inside the band and counted, however few bars that takes.
    assert sum(b["n"] for b in histogram["buckets"]) == MIN_DISTRIBUTION_ADS
    assert histogram["below"] == histogram["above"] == 0


@pytest.mark.django_db
def test_year_options_survive_a_year_too_thin_to_draw(cohorts):
    """Refusing to draw a distribution must not disable the year picker.

    The picker's options come from this response. Answer a thin year without
    them and the control that got the reader there empties and switches off,
    stranding them with no way back except discarding the model too.
    """
    model, brand = cohorts["model"], cohorts["brand"]
    for year, n in ((1399, MIN_DISTRIBUTION_ADS * 3), (1401, 2)):
        Ad.objects.bulk_create([
            Ad(
                code=f"t{year}n{i}", brand=brand, model=model, title=f"t{year}",
                year_jalali=year, current_price=1_000_000_000 + i * 1_000_000,
                publish_at=timezone.now(), last_seen_at=timezone.now(),
            )
            for i in range(n)
        ])

    thin = price_distribution(model_id=model.pk, year_jalali=1401)
    assert thin["available"] is False
    assert thin["reason"] == "insufficient_listings"
    # Both years still on offer, so 1399 is one click away.
    assert [y["year_jalali"] for y in thin["years"]] == [1399, 1401]
