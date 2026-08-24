"""What a car is worth, and everything that quotes the number.

Unit tests for the pure predicates and estimators; integration tests wherever
the subject is a queryset over stored rows — the deal board and the notifier are
both exactly that, so the only honest check builds a real cohort.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from apps.core import notify as N
from apps.core import pricing as FP
from apps.core import research as R
from apps.core.models import (
    Ad,
    Brand,
    City,
    DealScoreCache,
    ListingEpisode,
    Model,
    NotifiedAd,
    NotifierSettings,
    Variant,
)
from apps.core.pricing import compute_deal_scores
from apps.core.quality import condition_discounted, price_basis_unclear

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


# --- the predicate -----------------------------------------------------------

@pytest.mark.parametrize("text", [
    "مبلغ فوق، پیش پرداخت است",
    "فروش خودرو به صورت نقد و اقساط با سود بانک مرکزی",
    "ثبت نام محدود خودرو ۲۱۲، پرداخت طی چند مرحله",
    "پیش‌فروش با تحویل ۳ ماهه",
    "پرداخت ۳ مرحله ای برای اطلاعات بیشتر تماس بگیرید",
    "عاملیت فروش نمایندگی ها",
    "فروش با لیزینگ بدون ضامن",
])
def test_finance_vocabularies_are_all_caught(text):
    """Each of these appeared verbatim on a row the old board ranked top-15."""
    assert price_basis_unclear(description=text)


def test_the_structured_field_is_honoured_when_bama_sets_it():
    assert price_basis_unclear(price_type="installment")
    assert price_basis_unclear(prepayment=500_000_000)


def test_bama_labels_most_of_them_lumpsum():
    """Why the text rule exists at all.

    Dealers type the down payment into the cash-price box, so the structured
    field alone caught 41 of 200 contaminated rows where the text caught 148.
    """
    assert price_basis_unclear(
        price_type="lumpsum", description="پیش پرداخت ۵۰٪، اقساط ۳۶ ماهه"
    )


@pytest.mark.parametrize("text", [
    "بسیار تمیز، تمام رنگ‌ها سالم، کولر و فنی سلامت، فوری فروشی",
    "کیربوکس جدید، بیمه ۶ ماه، فنی درجه یک",
    "تودوزی نو، صندلی نو",
])
def test_clean_private_listings_survive(text):
    """The false-positive side, and the one that costs the product real deals.

    Measured cost of this rule on the healthy part of the board was 4% of the
    0–5% discount band; a rule that ate ordinary listings would be worse than
    the bug it fixes.
    """
    assert not price_basis_unclear(description=text)


@pytest.mark.parametrize("text", [
    "مدل ۹۷ تصادفی که قبل تصادف هیچ رنگی نداشته",
    "احتیاج به صافکاری دارد",
    "دوررنگ به دلیل زیبایی",
    "تمامی مناطق آزاد، تحویل فوری",
])
def test_condition_is_flagged_but_never_excluded(text):
    """A تصادفی car is really for sale at really that price.

    The cohort key is (model, variant, year) and has no condition dimension, so
    the gap is real and unexplained — the reader needs telling, not protecting.
    """
    assert condition_discounted(description=text)
    assert not price_basis_unclear(description=text)


# --- the exclusion ------------------------------------------------------------

@pytest.fixture
def cohort(db):
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو", is_confirmed=True)
    model = Model.objects.create(brand=brand, name_fa="207", is_confirmed=True)
    variant = Variant.objects.create(model=model, name_fa="پانوراما")
    city = City.objects.create(name_fa="تهران")

    def make(code, price, **kw):
        return Ad.objects.create(
            code=code, brand=brand, model=model, variant=variant, city=city,
            year_jalali=1402, mileage=50_000, current_price=price,
            status=Ad.Status.ACTIVE, title=kw.pop("title", "پژو، 207"),
            first_seen_at=NOW - timedelta(days=5), last_seen_at=NOW, publish_at=NOW,
            **kw,
        )

    # Ten peers at 2B so the cohort clears MIN_PEERS and the median is 2B.
    for i in range(10):
        make(f"peer{i:04d}", 2_000_000_000)
    return make


@pytest.mark.django_db
def test_an_installment_ad_never_reaches_the_board(cohort):
    """1.05B against a 2B median is a 47% "discount" and pure artifact."""
    cohort("instal01", 1_050_000_000,
           description="نقد و اقساط، پیش پرداخت ۵۰ درصد")

    compute_deal_scores()

    assert not DealScoreCache.objects.filter(ad_id="instal01").exists()


@pytest.mark.django_db
def test_a_genuinely_cheap_car_still_reaches_the_board(cohort):
    """The other half of the property: the filter must not empty the board."""
    cohort("cheap001", 1_700_000_000, description="بسیار تمیز، فنی سالم")

    compute_deal_scores()

    row = DealScoreCache.objects.get(ad_id="cheap001")
    assert row.discount_pct == pytest.approx(15.0, abs=0.1)


@pytest.mark.django_db
def test_a_damaged_car_stays_on_the_board(cohort):
    """Condition explains the gap; it does not disqualify the listing."""
    cohort("crash001", 1_500_000_000, description="تصادفی، شاسی سالم")

    compute_deal_scores()

    assert DealScoreCache.objects.filter(ad_id="crash001").exists()


@pytest.mark.django_db
def test_havaleh_is_still_excluded_after_the_special_case_was_removed(cohort):
    """The old rule was `title__startswith("حواله")`. The regex subsumes it."""
    cohort("havale01", 1_100_000_000, title="حواله پژو، 207")

    compute_deal_scores()

    assert not DealScoreCache.objects.filter(ad_id="havale01").exists()


@pytest.fixture(autouse=True)
def _episodes_are_trustworthy(settings):
    """Pin the clean-start cutoff behind these fixtures' episode dates.

    Survival only counts episodes started after removal detection became
    reliable (``BAMA_EPISODE_CLEAN_START``). These tests are about the
    Kaplan-Meier arithmetic, not that cutoff, so they opt out of it — the cutoff
    itself is covered by ``test_survival_excludes_episodes_from_the_dirty_era``.
    """
    settings.BAMA_EPISODE_CLEAN_START = "2000-01-01"


@pytest.fixture
def catalog(db):
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو", is_confirmed=True)
    model = Model.objects.create(brand=brand, name_fa="207", is_confirmed=True)
    return {
        "brand": brand, "model": model,
        "variant": Variant.objects.create(model=model, name_fa="پانوراما"),
        "city": City.objects.create(name_fa="تهران"),
        "city2": City.objects.create(name_fa="مشهد"),
    }


def make_ad(catalog, code, *, price=1_000_000_000, mileage=100_000, year=1400,
            city=None, status=Ad.Status.ACTIVE, first_seen=None):
    return Ad.objects.create(
        code=code, brand=catalog["brand"], model=catalog["model"],
        variant=catalog["variant"], city=city or catalog["city"],
        year_jalali=year, mileage=mileage, current_price=price, status=status,
        first_seen_at=first_seen or NOW - timedelta(days=30),
        last_seen_at=NOW, publish_at=first_seen or NOW - timedelta(days=30),
    )


def make_episode(ad, *, started, ended=None, price=None):
    return ListingEpisode.objects.create(
        ad=ad, started_at=started, ended_at=ended,
        first_price=price or ad.current_price, last_price=price or ad.current_price,
    )


# --- Kaplan-Meier -----------------------------------------------------------

def test_kaplan_meier_matches_a_hand_computed_curve():
    """Four listings, delisted on days 1, 2, 3, 4. At each step the survivor
    fraction is 1 - 1/at_risk, so the curve is 3/4, 1/2, 1/4, 0."""
    observations = [R.Observation(days=d, delisted=True) for d in (1, 2, 3, 4)]

    curve = R.kaplan_meier(observations)

    assert curve.survival == pytest.approx([0.75, 0.5, 0.25, 0.0])
    assert curve.median_days() == 2


def test_censoring_is_what_makes_this_different():
    """The property the naive average gets wrong.

    Ten cars: five delisted quickly, five still listed after a long time. The
    naive mean over *finished* listings sees only the fast five and reports a
    small number. The estimator keeps the slow five in the risk set, so survival
    never falls to 0.5 and the honest answer is "we do not know yet".

    This bias is one-directional and worst exactly when it matters: the slower the
    market, the more of it is unfinished and excluded.
    """
    fast = [R.Observation(days=d, delisted=True) for d in (1, 2, 3, 4, 5)]
    slow = [R.Observation(days=90, delisted=False) for _ in range(5)]

    naive = sum(o.days for o in fast) / len(fast)
    curve = R.kaplan_meier(fast + slow)

    assert naive == 3.0, "averaging only the finished listings"
    assert curve.censored == 5
    assert curve.median_days() == 5, "the still-listed cars hold the curve up"
    assert curve.median_days() > naive

    # Push the censoring further and the honest answer stops being a number at
    # all: with most of the market unfinished, survival never reaches 0.5 and
    # anything reported would be extrapolation.
    mostly_open = [R.Observation(days=d, delisted=True) for d in (1, 2, 3)] + [
        R.Observation(days=90, delisted=False) for _ in range(7)
    ]
    assert R.kaplan_meier(mostly_open).median_days() is None


def test_a_censored_listing_never_counts_as_a_delisting():
    only_open = [R.Observation(days=10, delisted=False) for _ in range(5)]

    curve = R.kaplan_meier(only_open)

    assert curve.delisted == 0
    assert curve.survival == []
    assert curve.median_days() is None


@pytest.mark.django_db
def test_survival_refuses_a_thin_cohort(catalog):
    for i in range(3):
        make_episode(make_ad(catalog, f"thin{i:04d}"), started=NOW - timedelta(days=10))

    result = R.survival(model_id=catalog["model"].pk)

    assert result["available"] is False
    assert result["reason"] == "insufficient_episodes"


@pytest.mark.django_db
def test_survival_reports_the_naive_number_alongside(catalog):
    """Shown, not asserted: the user can see the difference the method makes."""
    for i in range(15):
        ad = make_ad(catalog, f"done{i:04d}", status=Ad.Status.REMOVED)
        make_episode(ad, started=NOW - timedelta(days=20), ended=NOW - timedelta(days=17))
    for i in range(15):
        make_episode(make_ad(catalog, f"open{i:04d}"), started=NOW - timedelta(days=60))

    result = R.survival(model_id=catalog["model"].pk)

    assert result["available"] is True
    assert result["naive_mean_days_finished_only"] == pytest.approx(3.0, abs=0.2)
    assert result["censored"] == 15


@pytest.mark.django_db
def test_very_short_episodes_are_ignored(catalog):
    """A listing that appears and vanishes within hours is far more often a
    posting error or a moderation removal than a car that sold the same morning."""
    for i in range(25):
        ad = make_ad(catalog, f"blip{i:04d}", status=Ad.Status.REMOVED)
        make_episode(ad, started=NOW - timedelta(hours=2), ended=NOW - timedelta(hours=1))

    result = R.survival(model_id=catalog["model"].pk)

    assert result["available"] is False


# --- fair price -------------------------------------------------------------

@pytest.mark.django_db
def test_fair_price_explains_itself(catalog):
    """The reason this replaced a bare score: a number a user can argue with is
    worth more than one they cannot."""
    for i in range(20):
        make_ad(catalog, f"peer{i:04d}", price=1_000_000_000 + i * 1_000_000)
    make_ad(catalog, "target01", price=900_000_000)

    result = FP.fair_price("target01")

    assert result["available"] is True
    assert result["fair_value"] > 0
    assert [c["name"] for c in result["components"]][0] == "cohort_median"
    assert result["gap_pct"] < 0, "asking below fair value"
    assert result["confidence"] in {"low", "medium", "high"}


@pytest.mark.django_db
def test_fair_price_refuses_a_thin_cohort_rather_than_guessing(catalog):
    make_ad(catalog, "lonely01")

    result = FP.fair_price("lonely01")

    assert result["available"] is False
    assert result["reason"] == "insufficient_peers"


@pytest.mark.django_db
def test_fair_price_confidence_tracks_the_evidence(catalog):
    for i in range(10):
        make_ad(catalog, f"few{i:05d}", price=1_000_000_000)
    make_ad(catalog, "subject1", price=1_000_000_000)

    result = FP.fair_price("subject1")

    assert result["confidence"] == "low"
    assert result["peer_count"] == 11


@pytest.mark.django_db
def test_an_outlier_peer_does_not_set_the_fair_value(catalog):
    """Outliers are excluded from the baseline that judges believability — the
    same rule the cohort pass applies, honoured here."""
    for i in range(20):
        make_ad(catalog, f"norm{i:04d}", price=1_000_000_000)
    absurd = make_ad(catalog, "absurd01", price=90_000_000_000)
    Ad.objects.filter(code=absurd.code).update(cohort_flags=["price_outlier_high"])
    make_ad(catalog, "subject2", price=1_000_000_000)

    result = FP.fair_price("subject2")

    assert result["fair_value"] == pytest.approx(1_000_000_000, rel=0.05)


# --- retention --------------------------------------------------------------

@pytest.mark.django_db
def test_depreciation_curve_is_medians_not_a_fitted_line(catalog):
    """Cars lose value fastest early and flatten later, so a straight line
    overcharges high-mileage cars and undercharges low-mileage ones. A table of
    medians assumes no shape at all."""
    for year, price in ((1398, 600_000_000), (1400, 800_000_000), (1402, 1_000_000_000)):
        for i in range(10):
            make_ad(catalog, f"y{year}n{i:03d}", price=price + i * 1_000_000, year=year)

    curve = R.depreciation_curve(catalog["model"].pk)

    assert curve["available"] is True
    assert [p["year_jalali"] for p in curve["points"]] == [1398, 1400, 1402]
    assert curve["points"][0]["pct_of_newest"] == pytest.approx(60.0, abs=1.0)
    assert curve["reference_year"] == 1402


@pytest.mark.django_db
def test_a_thin_year_is_dropped_not_guessed(catalog):
    for i in range(10):
        make_ad(catalog, f"solid{i:04d}", year=1400)
    make_ad(catalog, "sparse01", year=1390)

    curve = R.depreciation_curve(catalog["model"].pk)

    assert curve["available"] is False, "one year with data is not a curve"


@pytest.mark.django_db
def test_survival_excludes_episodes_from_the_dirty_era(catalog, settings):
    """Episodes predating reliable removal detection are not evidence.

    Their end dates record when a sweep happened to finish, not when the car
    left the feed: endings landed on 17 of 39 days in lumps of up to 6,873, and
    every cohort of every model then returned a median of exactly 21.02 days.
    Excluded rather than deleted — they stay for provenance.
    """
    settings.BAMA_EPISODE_CLEAN_START = "2026-08-01"

    # Plenty of episodes, all from before the cutoff.
    for i in range(40):
        ad = make_ad(catalog, f"dirty{i:03d}", status=Ad.Status.REMOVED)
        make_episode(
            ad,
            started=datetime(2026, 7, 1, tzinfo=timezone.utc),
            ended=datetime(2026, 7, 22, tzinfo=timezone.utc),
        )

    result = R.survival(model_id=catalog["model"].pk)

    assert result["available"] is False
    assert result["reason"] == "insufficient_clean_history"
    assert result["n"] == 0
    assert result["excluded_episodes"] == 40
    assert result["clean_start"] == "2026-08-01"


# ==========================================================================
# The deal notifier
# ==========================================================================

@pytest.fixture
def notifier_catalog(db):
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو", is_confirmed=True)
    model = Model.objects.create(brand=brand, name_fa="207", is_confirmed=True)
    return {
        "brand": brand,
        "model": model,
        "variant": Variant.objects.create(model=model, name_fa="پانوراما"),
        "city": City.objects.create(name_fa="تهران"),
    }


@pytest.fixture
def cfg(db):
    settings = NotifierSettings.load()
    settings.enabled = True
    settings.telegram_chat_id = "12345"
    settings.save()
    return settings


def make_scored(notifier_catalog, code, *, discount=30.0, peers=20, price=1_000_000_000,
                model=None):
    ad = Ad.objects.create(
        code=code, brand=notifier_catalog["brand"], model=model or notifier_catalog["model"],
        variant=notifier_catalog["variant"], city=notifier_catalog["city"],
        year_jalali=1400, mileage=50_000, current_price=price,
        status=Ad.Status.ACTIVE, title="پژو، 207",
        first_seen_at=NOW - timedelta(days=1), last_seen_at=NOW, publish_at=NOW,
    )
    return DealScoreCache.objects.create(
        ad=ad, score=discount, discount_pct=discount, peer_median=price * 2,
        components={
            "peer_count": peers, "confidence": "high",
            "fair_value": price * 2, "price": price,
        },
    )


@pytest.fixture(autouse=True)
def _no_real_telegram(monkeypatch):
    """Never touch the network; record what would have been sent."""
    sent = []
    monkeypatch.setattr(N, "send_telegram", lambda text, chat_id: sent.append(text) or True)
    return sent


@pytest.mark.django_db
def test_disabled_notifier_sends_nothing(notifier_catalog, cfg):
    cfg.enabled = False
    cfg.save()
    make_scored(notifier_catalog, "deal0001")

    assert N.notify_deals() == {"enabled": False, "sent": 0, "candidates": 0}
    assert NotifiedAd.objects.count() == 0


@pytest.mark.django_db
def test_a_qualifying_deal_is_announced_once_ever(notifier_catalog, cfg):
    """The anti-noise property. A car that keeps qualifying is one piece of news."""
    make_scored(notifier_catalog, "deal0001", discount=30.0, peers=20)

    first = N.notify_deals()
    second = N.notify_deals()

    assert first["sent"] == 1
    assert second["sent"] == 0, "already announced"
    assert NotifiedAd.objects.count() == 1


@pytest.mark.django_db
def test_a_thin_cohort_is_never_announced(notifier_catalog, cfg):
    """A median over a handful of cars is not evidence of a bargain."""
    make_scored(notifier_catalog, "thin0001", discount=60.0, peers=cfg.min_peers - 1)

    assert N.notify_deals()["sent"] == 0


@pytest.mark.django_db
def test_a_bare_deposit_phrase_ad_is_never_announced(notifier_catalog, cfg):
    """The short form of the down-payment disclaimer, without "اقساط"."""
    row = make_scored(notifier_catalog, "deposit01", discount=60.0, peers=30)
    row.ad.description = "مبلغ فوق پیش پرداخت است"
    row.ad.save(update_fields=["description"])

    assert N.notify_deals()["sent"] == 0


def test_toman_never_uses_the_old_tenfold_divisor():
    assert N.toman(2_200_000_000) == "2.20B"
    assert N.toman(220_000_000) == "220M"


@pytest.mark.django_db
def test_a_shallow_discount_is_never_announced(notifier_catalog, cfg):
    make_scored(notifier_catalog, "weak0001", discount=cfg.min_discount_pct - 1, peers=30)

    assert N.notify_deals()["sent"] == 0


@pytest.mark.django_db
def test_price_bounds_scope_the_notifier(notifier_catalog, cfg):
    cfg.price_min = 2_000_000_000
    cfg.price_max = 5_000_000_000
    cfg.save()
    make_scored(notifier_catalog, "cheap001", price=1_000_000_000)
    make_scored(notifier_catalog, "inband01", price=3_000_000_000)
    make_scored(notifier_catalog, "dear0001", price=9_000_000_000)

    N.notify_deals()

    assert set(NotifiedAd.objects.values_list("ad_id", flat=True)) == {"inband01"}


@pytest.mark.django_db
def test_model_scope_restricts_to_chosen_models(notifier_catalog, cfg):
    other = Model.objects.create(
        brand=notifier_catalog["brand"], name_fa="206", is_confirmed=True
    )
    cfg.model_ids = [other.pk]
    cfg.save()
    make_scored(notifier_catalog, "wanted01", model=other)
    make_scored(notifier_catalog, "ignored1")

    N.notify_deals()

    assert set(NotifiedAd.objects.values_list("ad_id", flat=True)) == {"wanted01"}


@pytest.mark.django_db
def test_a_failed_send_is_retried_next_tick(notifier_catalog, cfg, monkeypatch):
    """Recording on a failed send would swallow the listing forever."""
    monkeypatch.setattr(N, "send_telegram", lambda text, chat_id: False)
    make_scored(notifier_catalog, "deal0001")

    result = N.notify_deals()

    assert result["candidates"] == 1
    assert result["sent"] == 0
    assert NotifiedAd.objects.count() == 0, "not marked as sent"


@pytest.mark.django_db
def test_dry_run_reports_without_sending(notifier_catalog, cfg, _no_real_telegram):
    make_scored(notifier_catalog, "deal0001")

    result = N.notify_deals(dry_run=True)

    assert result["candidates"] == 1
    assert result["sent"] == 0
    assert _no_real_telegram == []
    assert NotifiedAd.objects.count() == 0


@pytest.mark.django_db
def test_one_run_cannot_flood_the_chat(notifier_catalog, cfg):
    """A lowered threshold or a fresh install must not dump the board at once."""
    for i in range(N.MAX_PER_RUN + 5):
        make_scored(notifier_catalog, f"deal{i:04d}")

    assert N.notify_deals()["sent"] == N.MAX_PER_RUN


@pytest.mark.django_db
def test_message_names_the_evidence(notifier_catalog, cfg, _no_real_telegram):
    """A ping the reader cannot judge is a ping they learn to ignore."""
    make_scored(notifier_catalog, "deal0001", discount=30.0, peers=20)

    N.notify_deals()

    text = _no_real_telegram[0]
    assert "30% below fair value" in text
    assert "20 peers" in text
    assert "high confidence" in text


@pytest.mark.django_db
def test_settings_endpoint_round_trips(client, db):
    resp = client.patch(
        "/api/notifier-settings/",
        data={"enabled": True, "min_discount_pct": 25, "min_peers": 12},
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.json()["min_discount_pct"] == 25
    assert NotifierSettings.load().enabled is True


@pytest.mark.django_db
def test_settings_endpoint_rejects_a_peer_floor_below_the_engines(client, db):
    resp = client.patch(
        "/api/notifier-settings/",
        data={"min_peers": 3},
        content_type="application/json",
    )

    assert resp.status_code == 400
    assert "min_peers" in resp.json()


@pytest.mark.django_db
def test_settings_stay_a_singleton(db):
    NotifierSettings.load().save()
    NotifierSettings(min_peers=30).save()

    assert NotifierSettings.objects.count() == 1


@pytest.mark.django_db
def test_an_installment_ad_is_never_announced(notifier_catalog, cfg):
    """The audit's headline failure, as a regression test.

    74% of the top 200 board rows were installment listings advertising a down
    payment, and the notifier orders by discount — so these were the first
    messages a user would ever have received. Gated on the read side too because
    the cache is rebuilt on a schedule and can serve a stale row.
    """
    row = make_scored(notifier_catalog, "instal01", discount=48.0, peers=30)
    row.ad.description = "فروش خودرو به صورت نقد و اقساط، پیش پرداخت ۵۰٪"
    row.ad.save(update_fields=["description"])

    assert N.notify_deals()["sent"] == 0
    assert NotifiedAd.objects.count() == 0


@pytest.mark.django_db
def test_message_states_the_price_in_the_right_magnitude(notifier_catalog, cfg, _no_real_telegram):
    """This divided by 10_000_000 and labelled it "M", understating 10x.

    A 2.2B toman car was announced as "220M toman" while the same car read
    "2.20B" on the board it came from.
    """
    make_scored(notifier_catalog, "deal0042", discount=25.0, peers=30, price=2_200_000_000)

    N.notify_deals()

    (text,) = _no_real_telegram
    assert "2.20B toman" in text
    assert "220M" not in text
