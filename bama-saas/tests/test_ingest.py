"""Per-ad ingestion, and the listing lifecycle it drives.

Integration level: ingestion is defined by what lands in Postgres — the upsert,
the deduped version, the change-only price row — so these need the DB.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from datetime import timezone as tz

import pytest
from django.utils import timezone as djtz

from apps.core.models import (
    Ad,
    Brand,
    City,
    DealScoreCache,
    FetchRun,
    ListingEpisode,
    Model,
    PageCoverage,
    PriceObservation,
    Variant,
)
from apps.core.pricing import compute_deal_scores
from apps.jobs.ingest import (
    _MAX_GALLERY,
    _image_urls,
    ingest_ad,
    reset_cache,
    reset_price_cache,
)
from apps.jobs.jobs import (
    REQUIRED_MISSED_WINDOWS,
    backfill_images,
    link_reposts,
    mark_inactive,
    sweep_cutoff,
)
from apps.jobs.parsing import extract_ad, parse_publish_time
from tests.conftest import gallery

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=tz.utc)


def _ing(*args, **kwargs):
    """``ingest_ad`` returning just the ad.

    ``ingest_ad`` returns an ``IngestResult`` rather than the old
    ``(ad, created, price_changed)`` tuple, because the delta fetcher needs to
    know whether a new *version* appeared and the cohort pass needs the affected
    cohort. These two shims keep the tests that only care about the old three
    facts readable.
    """
    return ingest_ad(*args, **kwargs).ad


def _ing3(*args, **kwargs):
    """``ingest_ad`` as the legacy ``(ad, created, price_changed)`` triple."""
    r = ingest_ad(*args, **kwargs)
    return r.ad, r.created, r.price_changed


@pytest.fixture
def known_catalog(db):
    """The brand/model ``make_payload`` uses, already on record and confirmed.

    Ingestion flags any ad that brings a new Brand/Model into existence
    (``unknown_dimension``, see tests/test_catalog_guard.py). On an empty test
    database that is *every* ad, which would drown the assertions below that care
    about verification rules rather than catalog novelty.
    """
    from apps.core.models import Brand, Model

    brand = Brand.objects.create(slug="peugeot", name_fa="پژو", is_confirmed=True)
    Model.objects.create(brand=brand, name_fa="405", is_confirmed=True)
    reset_cache()
    return brand


@pytest.mark.django_db
def test_ingest_normalizes_year_and_zero_mileage(known_catalog, make_payload):
    """The two corruption bugs, pinned at the one place every import path shares.

    Bama sends model years in either calendar (this ad is Gregorian 2025) and
    sends "صفر کیلومتر" for brand-new cars, which the old parse_int(positive=True)
    turned into NULL for ~33% of all ads.
    """
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    observed = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    payload = make_payload("zerokm1", 15_000_000_000)
    payload["detail"]["year"] = "2025"
    payload["detail"]["mileage"] = "صفر کیلومتر"
    extracted = extract_ad(payload, observed)

    ad = _ing(extracted, run=run, observed_at=observed, publish_at=observed)
    ad.refresh_from_db()

    assert (ad.year_jalali, ad.year_gregorian, ad.year_calendar) == (1404, 2025, "gregorian")
    assert ad.year == 2025, "the raw value stays untouched for provenance"
    assert ad.mileage == 0, "zero-km must be 0, never NULL"
    assert ad.canonical_path
    assert ad.quality_flags == []


@pytest.mark.django_db
def test_ingest_never_persists_a_hard_rejected_ad(make_payload):
    """A lump-sum ad with no price is unusable and unrepairable, so it must not
    reach the Ad table at all — but the payload stays in IngestReject so the rule
    remains replayable if it turns out to be wrong."""
    from apps.core.models import IngestReject

    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    observed = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    payload = make_payload("badprice1", 0)  # lumpsum + price 0 -> hard failure
    extracted = extract_ad(payload, observed)

    ad, created, price_changed = _ing3(
        extracted, run=run, observed_at=observed, publish_at=observed
    )

    assert (ad, created, price_changed) == (None, False, False)
    assert not Ad.objects.filter(code="badprice1").exists()
    reject = IngestReject.objects.get(code="badprice1")
    assert reject.rule == "price_missing_for_lumpsum"
    assert reject.raw_payload, "the payload is retained so the rule can be replayed"


@pytest.mark.django_db
def test_ad_that_turns_bad_is_removed_not_left_stale(make_payload):
    """The re-insertion trap: purging a bad row is pointless if the next fetch
    puts it back, and equally pointless if a row that WAS good and has since gone
    bad keeps its old clean values. Both must resolve to "not in the table"."""
    observed = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    run1 = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    good = extract_ad(make_payload("flip123", 1_000_000_000), observed)
    ad, created, _ = _ing3(good, run=run1, observed_at=observed, publish_at=observed)
    assert created and ad is not None

    run2 = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    reset_price_cache()
    bad = extract_ad(make_payload("flip123", 1_500_000), observed)  # below the floor
    ad2 = _ing(bad, run=run2, observed_at=observed, publish_at=observed)

    assert ad2 is None
    ad_stored = Ad.objects.get(code="flip123")
    assert "price_too_low" in ad_stored.quality_flags
    assert PriceObservation.objects.filter(ad__code="flip123").count() > 0


@pytest.mark.django_db
def test_negotiable_zero_price_is_not_quarantined(known_catalog, make_payload):
    """21.6% of real ads are negotiable with price "0" — the single most
    important false positive to avoid."""
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    observed = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    payload = make_payload("nego123", 0)
    payload["price"]["type"] = "negotiable"
    extracted = extract_ad(payload, observed)

    ad = _ing(extracted, run=run, observed_at=observed, publish_at=observed)
    ad.refresh_from_db()

    assert ad.quality_flags == []


@pytest.mark.django_db
def test_ingest_creates_ad_version_and_price(make_payload):
    run = FetchRun.objects.create(source=FetchRun.Source.BULK_IMPORT)
    observed = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    extracted = extract_ad(make_payload("abc001", 1_000_000_000), observed)
    publish_at = parse_publish_time(extracted["publish_phrase"], observed)

    ad, created, price_changed = _ing3(
        extracted, run=run, observed_at=observed, publish_at=publish_at
    )

    assert created and price_changed
    assert ad.current_price == 1_000_000_000
    assert ad.first_seen_at == observed
    assert Ad.objects.count() == 1
    assert ad.versions.count() == 1
    assert PriceObservation.objects.count() == 1


@pytest.mark.django_db
def test_ingest_is_idempotent_unchanged_price(make_payload):
    run = FetchRun.objects.create(source=FetchRun.Source.BULK_IMPORT)
    observed = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    payload = make_payload("abc002", 1_000_000_000)
    extracted = extract_ad(payload, observed)
    publish_at = parse_publish_time(extracted["publish_phrase"], observed)

    ingest_ad(extracted, run=run, observed_at=observed, publish_at=publish_at)
    # Re-ingest the identical payload under a new run.
    run2 = FetchRun.objects.create(source=FetchRun.Source.BULK_IMPORT)
    reset_price_cache()
    _, created, price_changed = _ing3(
        extracted, run=run2, observed_at=observed, publish_at=publish_at
    )

    assert created is False  # snapshot upserted, not created
    assert price_changed is False  # change-only price dedup
    assert Ad.objects.count() == 1
    assert PriceObservation.objects.count() == 1


@pytest.mark.django_db
def test_ingest_records_price_change(make_payload):
    observed = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)

    run1 = FetchRun.objects.create(source=FetchRun.Source.BULK_IMPORT)
    e1 = extract_ad(make_payload("abc003", 1_000_000_000), observed)
    ingest_ad(e1, run=run1, observed_at=observed, publish_at=parse_publish_time(e1["publish_phrase"], observed))

    run2 = FetchRun.objects.create(source=FetchRun.Source.BULK_IMPORT)
    later = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    reset_price_cache()
    e2 = extract_ad(make_payload("abc003", 1_200_000_000), later)
    _, created, price_changed = _ing3(
        e2, run=run2, observed_at=later, publish_at=parse_publish_time(e2["publish_phrase"], later)
    )

    assert created is False
    assert price_changed is True
    assert Ad.objects.count() == 1
    prices = list(PriceObservation.objects.values_list("price", flat=True).order_by("observed_at"))
    assert prices == [1_000_000_000, 1_200_000_000]


# --- listing-presentation fields --------------------------------------------

@pytest.mark.django_db
def test_presentation_fields_are_promoted_from_the_payload(known_catalog, make_payload):
    """These are the evidence behind an outlier explanation: "priced far under
    its cohort, one photo, a two-line description, unverified seller" is an
    answer; the price alone is only a number."""
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    observed = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    payload = make_payload("present1", 15_000_000_000)
    payload["detail"].update({
        "image_count": "7",
        "description": "x" * 120,
        "authenticated": True,
        "modified_date": "2026-05-13T12:30:58.32",
    })

    ad = _ing(extract_ad(payload, observed), run=run, observed_at=observed, publish_at=observed)
    ad.refresh_from_db()

    assert ad.image_count == 7
    assert ad.description_length == 120
    assert ad.seller_authenticated is True
    assert ad.source_modified_at is not None


# --- derived flag on the UPDATE path -----------------------------------------

@pytest.mark.django_db
def test_reingest_sets_price_basis_unclear_through_update(known_catalog, make_payload):
    """queryset.update() never calls Ad.save, so the derived flag used to stick.

    An ad first seen as cash that later becomes an instalment listing must flip
    on the next crawl. Leaving the old False is the exact staleness this column
    exists to end — and is what the UPDATE path in ingest used to do.
    """
    observed = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    run1 = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    payload = make_payload("instal01", 15_000_000_000)
    ad = _ing(extract_ad(payload, observed), run=run1,
              observed_at=observed, publish_at=observed)
    ad.refresh_from_db()
    assert ad.price_basis_unclear is False

    run2 = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    reset_price_cache()
    later = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    # Title stays the original "پژو، 405" so extract_ad still resolves the
    # known model. The finance vocabulary lives in the description — that is
    # where Bama actually puts instalment terms on lumpsum ads, and it is
    # enough for quality.price_basis_unclear.
    payload["detail"]["description"] = "فروش خودرو به صورت نقد و اقساط"
    _ing(extract_ad(payload, later), run=run2, observed_at=later, publish_at=later)

    stored = Ad.objects.get(code="instal01")
    assert stored.price_basis_unclear is True
    assert stored.description.startswith("فروش خودرو به صورت نقد و اقساط")


# --- photos ------------------------------------------------------------------
#
# Unit level for the extractor (a pure dict -> tuple function), integration for
# the backfill (its whole subject is rows already in the database).

_CDN = "https://cdn-sth1.bama.ir/uploads/BamaImages/VehicleCarImages"


def _gallery_payload(n: int = 3) -> dict:
    """A payload shaped the way the live feed sends one: gallery at the TOP
    level, a single thumbnail on `detail`."""
    return {
        "detail": {"code": "img1", "image": f"{_CDN}/x/detail.jpg?x-img=resize,w_450"},
        "images": [
            {"large": f"{_CDN}/x/{i}.jpg?x-img=resize,w_600",
             "small": f"{_CDN}/x/{i}.jpg?x-img=resize,w_450",
             "thumb": f"{_CDN}/x/{i}.jpg?x-img=resize,w_90"}
            for i in range(n)
        ],
    }


def test_gallery_is_read_from_the_top_level_not_from_detail():
    """`detail` carries one thumbnail; the gallery is a level up.

    This was handed `detail` alone, so _MAX_GALLERY had never once applied and
    every listing in the database had at most one photo.
    """
    primary, gallery = _image_urls(_gallery_payload(3))

    assert len(gallery) == 3
    assert all("w_600" in url for url in gallery)     # detail-page size
    assert "w_450" in primary                          # card size


def test_a_payload_with_only_detail_image_still_yields_a_photo():
    """The shape behind the 14,658 rows that render "No photo" today."""
    primary, gallery = _image_urls(
        {"detail": {"image": f"{_CDN}/x/only.jpg?x-img=resize,w_450"}}
    )
    assert primary.startswith(_CDN)
    assert gallery == [primary]


def test_a_non_bama_host_is_refused():
    primary, gallery = _image_urls(
        {"images": [{"large": "https://evil.example.com/x.jpg"}],
         "detail": {"image": "http://cdn-sth1.bama.ir/insecure.jpg"}}
    )
    assert (primary, gallery) == ("", [])


def test_gallery_is_capped():
    _, gallery = _image_urls(_gallery_payload(40))
    assert len(gallery) == _MAX_GALLERY


@pytest.mark.django_db
def test_backfill_fills_photos_from_payloads_already_stored(known_catalog):
    """No re-crawl: the URLs are already inside raw_payload, unread."""
    Ad.objects.create(
        code="backfill1", title="x", current_price=1_000_000_000,
        primary_image_url="", image_urls=[], raw_payload=_gallery_payload(3),
    )

    first = backfill_images()
    assert first["filled"] == 1

    ad = Ad.objects.get(code="backfill1")
    assert ad.primary_image_url
    assert len(ad.image_urls) == 3

    # Idempotent: a filled row is no longer a candidate, so a second pass is a
    # no-op rather than rewriting the same bytes.
    assert backfill_images()["filled"] == 0


@pytest.mark.django_db
def test_backfill_deletes_what_it_cannot_fill(known_catalog):
    """The feed is crawled with image=1, so a photoless ad is out of population.

    Not "kept with a placeholder": these are rows the crawl never meant to
    collect, and on the board they were cards with nothing to show.
    """
    Ad.objects.create(
        code="nophoto1", title="x", current_price=1_000_000_000,
        primary_image_url="", raw_payload={"detail": {"code": "nophoto1"}},
    )
    result = backfill_images()

    assert result["filled"] == 0
    # Ads, not CASCADEd rows: the first production run reported 155,240 for the
    # 8,889 ads it actually removed, because `.delete()` counts everything it
    # reached through observations, versions, episodes and prices.
    assert result["pruned"] == 1
    assert not Ad.objects.filter(code="nophoto1").exists()


@pytest.mark.django_db
def test_backfill_fills_before_it_prunes(known_catalog):
    """Order matters: reversed, this would delete every row whose photo was
    merely unread — ~28,500 of them in production."""
    Ad.objects.create(
        code="fillme01", title="x", current_price=1_000_000_000,
        primary_image_url="", image_urls=[], raw_payload=_gallery_payload(2),
    )
    Ad.objects.create(
        code="dropme01", title="x", current_price=1_000_000_000,
        primary_image_url="", raw_payload={"detail": {"code": "dropme01"}},
    )

    result = backfill_images()

    assert result == {"scanned": 2, "filled": 1, "pruned": 1, "remaining": 0}
    assert Ad.objects.filter(code="fillme01").exists()
    assert not Ad.objects.filter(code="dropme01").exists()


@pytest.mark.django_db
def test_backfill_can_fill_without_pruning(known_catalog):
    """`prune=False` is the dry-run for "what would this remove?"."""
    Ad.objects.create(
        code="nophoto2", title="x", current_price=1_000_000_000,
        primary_image_url="", raw_payload={"detail": {"code": "nophoto2"}},
    )
    result = backfill_images(prune=False)

    assert result["pruned"] == 0
    assert Ad.objects.filter(code="nophoto2").exists()


@pytest.mark.django_db
def test_missing_presentation_fields_stay_null(known_catalog, make_payload):
    """None means "not stated", which is a different fact from zero — a seller
    who wrote no description and one whose description we failed to read must
    stay distinguishable or every average over them is wrong."""
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    observed = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)

    ad = _ing(
        extract_ad(make_payload("present2", 15_000_000_000), observed),
        run=run, observed_at=observed, publish_at=observed,
    )
    ad.refresh_from_db()

    assert ad.description_length is None
    assert ad.seller_authenticated is None
    assert ad.source_modified_at is None
    # image_count is NOT in this list any more. It used to be, back when an ad
    # could have no photo at all; `_photo_missing` now makes that unstorable, so
    # when Bama omits the count the gallery we did read is the better answer
    # than None.
    assert ad.image_count == 3


@pytest.mark.django_db
def test_a_malformed_timestamp_never_costs_us_the_ad(known_catalog, make_payload):
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    observed = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    payload = make_payload("present3", 15_000_000_000)
    payload["detail"]["modified_date"] = "yesterday-ish"

    ad = _ing(extract_ad(payload, observed), run=run, observed_at=observed, publish_at=observed)

    assert ad is not None
    assert ad.source_modified_at is None


def test_source_timestamps_are_read_as_tehran_local():
    """Bama sends a bare local timestamp with no offset. Reading it as UTC would
    shift every value by Tehran's offset — an error that never surfaces because
    the result still looks like a plausible date."""
    from apps.jobs.ingest import parse_source_datetime

    parsed = parse_source_datetime("2026-05-13T12:30:58.32")

    assert parsed.tzinfo is not None
    assert parsed.hour != 12, "a bare local time must not be taken for UTC"


# ---------------------------------------------------------------------------
# A make is a make; a model filed as one is not
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_a_model_bama_files_as_a_brand_lands_under_its_real_make(make_payload):
    """سمند and پراید arrive as top-level brands and must not stay that way.

    Bama's feed has no manufacturer field, so every IKCO and SAIPA model is its
    own one-model "brand". That splits the two makes covering most of the market
    into a dozen fragments each and leaves the brand filter unable to answer
    "show me Iran Khodro". ``BRAND_PARENT`` remaps them on the way in; the model
    name is what carries the identity, so nothing is lost by the merge.
    """
    reset_cache()  # the dimension cache outlives a test database
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    for code, brand, model in [
        ("ikco001", "سمند", "سمند LX"),
        ("saipa01", "پراید", "پراید ۱۳۱"),
        ("chery01", "چری", "آریزو ۵"),
    ]:
        payload = make_payload(code, 3_000_000_000, brand=brand, model=model)
        extracted = extract_ad(payload, NOW)
        _ing(extracted, run=run, observed_at=NOW, publish_at=NOW)

    def brand_of(code):
        return Ad.objects.get(code=code).brand.name_fa

    assert brand_of("ikco001") == "ایران خودرو"
    assert brand_of("saipa01") == "سایپا"
    assert brand_of("chery01") == "چری", "a real make must pass through untouched"
    assert Ad.objects.get(code="ikco001").model.name_fa == "سمند LX", (
        "the model keeps the identity the brand column gave up"
    )
    assert not Brand.objects.filter(name_fa__in=["سمند", "پراید"]).exists()


# ---------------------------------------------------------------------------
# One bad ad must not take its page — or its run — down with it
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_integrity_error_on_one_ad_does_not_lose_the_rest_of_the_page(
    known_catalog, monkeypatch
, make_payload):
    """The failure that ended whole sweeps in production.

    The fetcher ingests a page inside one transaction, so before per-ad
    savepoints a single ad that violated a DB constraint rolled back every
    other ad on the page and failed the run. Observed live as a NOT NULL
    violation on ``description`` and an FK violation on
    ``AdObservation.version_id``.
    """
    from django.db import IntegrityError, transaction

    from apps.core.models import IngestReject
    from apps.jobs import ingest as ingest_mod

    real_get_or_create = ingest_mod.AdVersion.objects.get_or_create

    def explode_for_bad_ad(*args, **kwargs):
        if kwargs.get("ad") is not None and kwargs["ad"].code == "bad00002":
            raise IntegrityError('null value in column "description"')
        return real_get_or_create(*args, **kwargs)

    monkeypatch.setattr(
        ingest_mod.AdVersion.objects, "get_or_create", explode_for_bad_ad
    )

    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    observed = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    codes = ["good0001", "bad00002", "good0003"]

    # One transaction for the whole "page", exactly as the fetcher does it.
    with transaction.atomic():
        results = [
            ingest_ad(
                extract_ad(make_payload(code, 15_000_000_000), observed),
                run=run,
                observed_at=observed,
                publish_at=observed,
            )
            for code in codes
        ]

    assert [r.rejected for r in results] == [False, True, False]
    assert set(Ad.objects.values_list("code", flat=True)) == {"good0001", "good0003"}
    assert IngestReject.objects.filter(
        code="bad00002", rule="integrity_error"
    ).exists(), "the bad payload must be quarantined, not silently dropped"


@pytest.mark.django_db
def test_rolled_back_version_is_dropped_from_the_cache(known_catalog, monkeypatch, make_payload):
    """The FK violation's actual root cause.

    ``_VERSION_CACHE`` holds ``AdVersion`` objects that outlive the transaction
    that made them. When a write rolled back, the cache still handed out a
    version whose row was gone, and the next sighting of that ad inserted an
    observation pointing at a nonexistent id — taking the next run down too.
    """
    from django.db import IntegrityError

    from apps.jobs import ingest as ingest_mod

    observed = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)

    def explode(*args, **kwargs):
        raise IntegrityError("boom")

    monkeypatch.setattr(ingest_mod.AdObservation.objects, "get_or_create", explode)
    ingest_ad(
        extract_ad(make_payload("cached01", 15_000_000_000), observed),
        run=run, observed_at=observed, publish_at=observed,
    )

    assert not [k for k in ingest_mod._VERSION_CACHE if k[0] == "cached01"], (
        "a version created inside a rolled-back savepoint must not stay cached"
    )


@pytest.fixture(autouse=True)
def _reset_ingest_caches():
    """Both ingest caches are process-global and survive test rollback.

    `resolve_dimensions` memoises City/Brand primary keys; after a test's
    transaction rolls back those ids no longer exist, and the next test's insert
    fails on the foreign key.
    """
    from apps.jobs.ingest import reset_cache, reset_price_cache

    reset_cache()
    reset_price_cache()
    yield
    reset_cache()
    reset_price_cache()


@pytest.fixture
def catalog(db):
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو")
    model = Model.objects.create(brand=brand, name_fa="پژو ۲۰۶")
    variant = Variant.objects.create(model=model, name_fa="تیپ ۵")
    city = City.objects.create(name_fa="تهران")
    return {"brand": brand, "model": model, "variant": variant, "city": city}


FEED_DEPTH = 100


def _cover(at, lo=1, hi=FEED_DEPTH, run=None):
    """Record that ranks ``lo..hi`` were fetched at ``at``.

    Coverage, not run status, is what the removal rule reads now, so these
    tests seed PageCoverage directly. ``run`` is shared between calls when a
    test needs one run to contribute several disjoint ranges.
    """
    run = run or FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH,
        status=FetchRun.Status.SUCCEEDED,
        started_at=at,
    )
    PageCoverage.objects.create(
        fetch_run=run,
        page_index=(lo - 1) // 30,
        rank_lo=lo,
        rank_hi=hi,
        ad_count=hi - lo + 1,
        new_count=0,
        changed_count=0,
        fetched_at=at,
    )
    return run


def _ad(catalog, code, last_seen, *, price=1_000_000_000, mileage=100_000,
        seen_recently=False):
    """One ACTIVE listing.

    `seen_recently` decouples "when was this published" from "when did the
    crawler last see it", because this file needs both. The removal tests are
    *about* `last_seen` and pass an arbitrary past instant, which must land on
    the row unchanged. The pricing test needs a cohort that looks live, because
    confidence now reads cohort freshness (`pricing.COHORT_STALE_AFTER`) and a
    cohort last seen weeks ago legitimately reads as stale — so it opts in
    rather than the default quietly rewriting what every other test asserts on.
    """
    return Ad.objects.create(
        code=code, brand=catalog["brand"], model=catalog["model"],
        variant=catalog["variant"], city=catalog["city"],
        year=1399, year_jalali=1399, year_calendar="jalali",
        mileage=mileage, current_price=price,
        publish_at=last_seen, first_seen_at=last_seen - timedelta(days=10),
        last_seen_at=djtz.now() if seen_recently else last_seen,
        status=Ad.Status.ACTIVE,
    )


# ---------------------------------------------------------------------------
# Coverage-based removal
#
# The rule reads accumulated PageCoverage over two consecutive windows, not
# fetch-run status. Windows are relative to wall-clock now, so these seed times
# are offsets from `timezone.now()` rather than the fixed NOW above.
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_no_removal_with_one_covered_window(catalog):
    """The critical safety case: one window must never empty the market.

    A wall-clock rule marks everything REMOVED the moment the crawler stalls
    long enough. The coverage rule must refuse to act, because a single pass
    proves nothing about ads it may have missed to rank shift.

    Refusing to say REMOVED is not the same as saying ACTIVE, though: the ad
    lands in UNVERIFIED, which is what stops the app from advertising a car
    nobody has seen in a month as being for sale.
    """
    now = djtz.now()
    _cover(now - timedelta(hours=2))
    stale = _ad(catalog, "stale001", now - timedelta(days=30))

    mark_inactive()

    stale.refresh_from_db()
    assert stale.status == Ad.Status.UNVERIFIED
    cutoff, n = sweep_cutoff()
    assert cutoff is None
    assert n == 1


@pytest.mark.django_db
def test_no_removal_without_any_coverage(catalog):
    stale = _ad(catalog, "stale002", djtz.now() - timedelta(days=90))
    mark_inactive()
    stale.refresh_from_db()
    assert stale.status == Ad.Status.UNVERIFIED


@pytest.mark.django_db
def test_ad_absent_from_two_covered_windows_is_removed(catalog):
    now = djtz.now()
    _cover(now - timedelta(hours=2))    # recent window
    _cover(now - timedelta(hours=30))   # older window

    gone = _ad(catalog, "gone0001", now - timedelta(hours=50))

    mark_inactive()

    gone.refresh_from_db()
    assert gone.status == Ad.Status.REMOVED
    # removed_at is stamped with the ad's own last sighting, not "now" — that
    # is the best estimate of when it actually left the feed.
    assert gone.removed_at == gone.last_seen_at


@pytest.mark.django_db
def test_ad_seen_inside_the_windows_survives(catalog):
    now = djtz.now()
    _cover(now - timedelta(hours=2))
    _cover(now - timedelta(hours=30))

    survivor = _ad(catalog, "alive001", now - timedelta(hours=3))

    mark_inactive()

    survivor.refresh_from_db()
    assert survivor.status == Ad.Status.ACTIVE


@pytest.mark.django_db
def test_partial_coverage_does_not_authorise_removal(catalog):
    """A window that missed part of the feed proves nothing about it.

    The older window walked the whole feed, so the depth ratchet knows it is
    100 deep; the recent window only reached rank 50. Ranks 51-100 were seen by
    nobody recently, so no ad may be declared gone.
    """
    now = djtz.now()
    _cover(now - timedelta(hours=30), lo=1, hi=FEED_DEPTH)
    _cover(now - timedelta(hours=2), lo=1, hi=50)

    stale = _ad(catalog, "stale003", now - timedelta(days=30))
    mark_inactive()

    stale.refresh_from_db()
    assert stale.status == Ad.Status.UNVERIFIED
    assert sweep_cutoff()[0] is None


@pytest.mark.django_db
def test_coverage_unions_across_several_partial_runs(catalog):
    """The property the whole redesign exists for.

    Four interrupted runs that each walked a quarter of the feed prove exactly
    what one uninterrupted sweep proved. Under the old rule none of these set
    reached_end, so removal detection stalled and delisted ads stayed ACTIVE
    for days — which is what batched listing episodes into lumps.
    """
    now = djtz.now()
    for window_hours in (30, 2):
        at = now - timedelta(hours=window_hours)
        for lo in (1, 26, 51, 76):
            _cover(at, lo=lo, hi=lo + 24)   # a separate run each time

    gone = _ad(catalog, "gone0002", now - timedelta(hours=50))
    mark_inactive()

    gone.refresh_from_db()
    assert gone.status == Ad.Status.REMOVED


@pytest.mark.django_db
def test_days_override_bypasses_coverage_rule(catalog):
    """The escape hatch still works with no coverage on record at all."""
    stale = _ad(catalog, "stale005", djtz.now() - timedelta(days=30))
    mark_inactive(days=1)
    stale.refresh_from_db()
    assert stale.status == Ad.Status.REMOVED


@pytest.mark.django_db
def test_cutoff_is_the_start_of_the_older_window(catalog):
    now = djtz.now()
    _cover(now - timedelta(hours=2))
    _cover(now - timedelta(hours=30))
    cutoff, n = sweep_cutoff()
    assert n == REQUIRED_MISSED_WINDOWS
    # Two 24h windows back, give or take the second spent running the test.
    assert abs((cutoff - (now - timedelta(hours=48))).total_seconds()) < 60


# ---------------------------------------------------------------------------
# Lifecycle timestamps must stay monotonic under out-of-order ingestion
# ---------------------------------------------------------------------------

def _payload(code, price=1_000_000_000):
    return {
        "images": gallery(code),
        "detail": {
            "code": code, "title": "پژو، ۲۰۶", "brand_fa": "پژو",
            "year": "1399", "mileage": "100,000", "type": "car",
            "time": "۲ ساعت پیش", "url": f"https://bama.ir/cad/{code}",
            "location": "تهران", "transmission": "دنده‌ای",
        },
        "price": {"price": str(price), "type": "lumpsum",
                  "payment": "0", "prepayment": "0", "installments": "0"},
    }


@pytest.mark.django_db
def test_stale_observation_does_not_move_last_seen_backwards(catalog):
    """import_history / crawl_gaps replay old pages; the bounds must not follow.

    `observed_at` is not monotonic across ingest calls, and writing it straight
    into last_seen_at put 5,009 production ads in a state where
    last_seen_at < first_seen_at and every duration came out negative.
    """
    from apps.jobs.ingest import ingest_ad
    from apps.jobs.parsing import extract_ad

    recent = NOW
    older = NOW - timedelta(days=10)
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)

    ingest_ad(extract_ad(_payload("mono0001"), recent), run=run,
              observed_at=recent, publish_at=recent)
    # A second run replaying an OLDER sighting of the same ad.
    run2 = FetchRun.objects.create(source=FetchRun.Source.HISTORY_REPLAY)
    ingest_ad(extract_ad(_payload("mono0001"), older), run=run2,
              observed_at=older, publish_at=older)

    ad = Ad.objects.get(code="mono0001")
    assert ad.last_seen_at == recent      # not dragged back to `older`
    assert ad.first_seen_at == older      # widened to the earliest sighting
    assert ad.last_seen_at >= ad.first_seen_at


@pytest.mark.django_db
def test_stale_observation_does_not_resurrect_a_removed_ad(catalog):
    """A backfill of old pages must not undo mark_inactive_ads."""
    from apps.jobs.ingest import ingest_ad
    from apps.jobs.parsing import extract_ad

    recent = NOW
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    ingest_ad(extract_ad(_payload("mono0002"), recent), run=run,
              observed_at=recent, publish_at=recent)

    Ad.objects.filter(code="mono0002").update(
        status=Ad.Status.REMOVED, removed_at=recent
    )

    run2 = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    ingest_ad(extract_ad(_payload("mono0002"), recent - timedelta(days=5)),
              run=run2, observed_at=recent - timedelta(days=5),
              publish_at=recent - timedelta(days=5))

    ad = Ad.objects.get(code="mono0002")
    assert ad.status == Ad.Status.REMOVED
    assert ad.removed_at is not None


@pytest.mark.django_db
def test_fresh_observation_still_reactivates(catalog):
    """The guard must not break the legitimate case: a genuinely re-seen ad."""
    from apps.jobs.ingest import ingest_ad
    from apps.jobs.parsing import extract_ad

    older = NOW - timedelta(days=5)
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    ingest_ad(extract_ad(_payload("mono0003"), older), run=run,
              observed_at=older, publish_at=older)
    Ad.objects.filter(code="mono0003").update(
        status=Ad.Status.REMOVED, removed_at=older
    )

    run2 = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    ingest_ad(extract_ad(_payload("mono0003"), NOW), run=run2,
              observed_at=NOW, publish_at=NOW)

    ad = Ad.objects.get(code="mono0003")
    assert ad.status == Ad.Status.ACTIVE
    assert ad.removed_at is None


# ---------------------------------------------------------------------------
# Mileage-adjusted deal score
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_thin_cohort_is_not_scored(catalog):
    """MIN_PEERS=8: a median of seven cars is not a deal board."""
    for i in range(7):
        _ad(catalog, f"thin{i:03d}", NOW, price=1_000_000_000, mileage=100_000)

    compute_deal_scores()

    assert DealScoreCache.objects.count() == 0


@pytest.mark.django_db
def test_asking_above_peer_median_is_not_a_deal(catalog):
    for i in range(8):
        _ad(catalog, f"peer{i:03d}", NOW, price=1_000_000_000, mileage=100_000)
    expensive = _ad(catalog, "pricey01", NOW, price=1_200_000_000, mileage=100_000)

    compute_deal_scores()

    assert DealScoreCache.objects.filter(ad_id=expensive.code).first() is None


@pytest.mark.django_db
def test_honest_discount_is_scored_with_evidence(catalog):
    # `seen_recently`: confidence reads cohort freshness, and this cohort is
    # meant to be live. Without it the fixture's fixed NOW ages past
    # COHORT_STALE_AFTER and the badge drops a tier for a reason the test is
    # not about.
    for i in range(8):
        _ad(catalog, f"peer{i:03d}", NOW, price=1_000_000_000, mileage=100_000,
            seen_recently=True)
    cheap = _ad(catalog, "bargain1", NOW, price=800_000_000, mileage=100_000,
                seen_recently=True)

    compute_deal_scores()

    row = DealScoreCache.objects.get(ad_id=cheap.code)
    assert row.discount_pct == pytest.approx(20.0, abs=0.5)
    assert row.peer_median == 1_000_000_000
    assert row.components["peer_count"] == 9
    assert row.components["confidence"] == "low"
    assert row.components["fair_value"] > 0
    assert cheap.current_price < row.peer_median


@pytest.mark.django_db
def test_ask_below_half_peer_median_is_not_a_deal(catalog):
    """80M against a 1.7B cohort is a deposit/typo, not a 95% discount."""
    for i in range(8):
        _ad(catalog, f"peer{i:03d}", NOW, price=1_700_000_000, mileage=100_000)
    deposit = _ad(catalog, "deposit1", NOW, price=80_000_000, mileage=100_000)

    compute_deal_scores()

    assert DealScoreCache.objects.filter(ad_id=deposit.code).first() is None


# ---------------------------------------------------------------------------
# Lifecycle: what state an absent ad lands in, and why we think it left
#
# Integration level: the subject is the row that survives a `mark_inactive`
# pass, and the decision depends on stored PageCoverage that no unit test of the
# function alone could supply.
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_an_unverified_ad_resolves_once_coverage_is_proven(catalog):
    """UNVERIFIED is a holding state, not a destination.

    An ad parked there while the crawler was blind must resolve as soon as two
    complete windows exist — otherwise fixing coverage would leave a permanent
    residue of ads stuck in limbo.
    """
    now = djtz.now()
    _cover(now - timedelta(hours=2))
    gone = _ad(catalog, "limbo001", now - timedelta(hours=50))

    mark_inactive()
    gone.refresh_from_db()
    assert gone.status == Ad.Status.UNVERIFIED

    _cover(now - timedelta(hours=30))    # the older window is now covered too
    mark_inactive()

    gone.refresh_from_db()
    assert gone.status == Ad.Status.REMOVED
    assert gone.removed_at == gone.last_seen_at


@pytest.mark.django_db
def test_being_seen_again_clears_the_inference(catalog, make_payload):
    """A car we can see is not "likely sold"."""
    now = djtz.now()
    ad = _ad(catalog, "back0001", now - timedelta(hours=50))
    Ad.objects.filter(code="back0001").update(
        status=Ad.Status.REMOVED, removed_at=now,
        likely_reason=Ad.Reason.SOLD, reason_confidence=Ad.Confidence.MEDIUM,
    )

    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH,
                                  status=FetchRun.Status.SUCCEEDED)
    payload = make_payload("back0001", 1_000_000_000)
    ingest_ad(extract_ad(payload, djtz.now()), run=run, observed_at=djtz.now(),
              publish_at=djtz.now())

    ad.refresh_from_db()
    assert ad.status == Ad.Status.ACTIVE
    assert ad.removed_at is None
    assert ad.likely_reason == ""
    assert ad.reason_confidence == ""


@pytest.mark.django_db
def test_a_delisted_ad_without_a_measured_baseline_is_low_confidence(catalog):
    """With no closed-episode history there is no P90 to judge tenure against,
    so "likely sold" is a guess and has to be labelled as one."""
    now = djtz.now()
    _cover(now - timedelta(hours=2))
    _cover(now - timedelta(hours=30))
    gone = _ad(catalog, "guess001", now - timedelta(hours=50))

    mark_inactive()

    gone.refresh_from_db()
    assert gone.likely_reason == Ad.Reason.SOLD
    assert gone.reason_confidence == Ad.Confidence.LOW


@pytest.mark.django_db
def test_a_repost_is_linked_and_the_predecessor_says_so(catalog):
    """Bama issues a new code for a relist, so the pair reads as one removal
    plus one arrival unless something ties them together."""
    now = djtz.now()
    old = _ad(catalog, "orig0001", now - timedelta(days=5))
    Ad.objects.filter(code="orig0001").update(
        status=Ad.Status.REMOVED, listing_fingerprint="samecar",
    )
    new = _ad(catalog, "relist01", now)
    Ad.objects.filter(code="relist01").update(
        listing_fingerprint="samecar", first_seen_at=now - timedelta(days=1),
    )

    assert link_reposts()["linked"] == 1

    new.refresh_from_db()
    old.refresh_from_db()
    assert new.reposted_from_id == "orig0001"
    assert old.likely_reason == Ad.Reason.REPOSTED
    assert old.reason_confidence == Ad.Confidence.HIGH
    # Nothing is merged away: both rows remain independently queryable.
    assert Ad.objects.filter(code__in=["orig0001", "relist01"]).count() == 2


@pytest.mark.django_db
def test_two_live_ads_with_the_same_spec_are_not_fused(catalog):
    """Two identically-specced cars in one city do exist. The predecessor must
    have already left before its successor appeared, or this is not a repost."""
    now = djtz.now()
    _ad(catalog, "twin0001", now)
    _ad(catalog, "twin0002", now)
    Ad.objects.all().update(listing_fingerprint="samecar",
                            first_seen_at=now - timedelta(days=2))

    assert link_reposts()["linked"] == 0
    assert Ad.objects.filter(reposted_from__isnull=False).count() == 0


@pytest.mark.django_db
def test_a_blank_fingerprint_never_matches(catalog):
    """An ad too thin to identify must not be linked to another one just as thin."""
    now = djtz.now()
    _ad(catalog, "thin0001", now - timedelta(days=5))
    Ad.objects.filter(code="thin0001").update(status=Ad.Status.REMOVED)
    _ad(catalog, "thin0002", now)

    assert Ad.objects.filter(listing_fingerprint="").count() == 2
    assert link_reposts()["linked"] == 0


@pytest.mark.django_db
def test_an_unverified_ad_keeps_its_episode_open(catalog):
    """UNVERIFIED means we lost coverage, not that the listing ended.

    Closing the episode here would stamp an ended_at nobody observed — and
    _expiry_threshold_days measures exactly these closed episodes, so unproven
    closures would feed back in as evidence for "likely expired".
    """
    from apps.jobs.jobs import sync_episodes

    now = djtz.now()
    ad = _ad(catalog, "open0001", now - timedelta(hours=50))
    sync_episodes()
    assert ListingEpisode.objects.get(ad=ad).ended_at is None

    _cover(now - timedelta(hours=2))          # one window only -> unprovable
    mark_inactive()
    ad.refresh_from_db()
    assert ad.status == Ad.Status.UNVERIFIED

    sync_episodes()
    assert ListingEpisode.objects.get(ad=ad).ended_at is None

    # Once absence IS proven, the episode closes as normal.
    _cover(now - timedelta(hours=30))
    mark_inactive()
    sync_episodes()
    assert ListingEpisode.objects.get(ad=ad).ended_at is not None


@pytest.mark.django_db
def test_a_crawler_stall_does_not_read_as_an_inventory_collapse(catalog):
    """UNVERIFIED ads still count as market inventory.

    Dropping them would report our own downtime as a shrinking market, and
    market_index is chained arithmetic over these rows.
    """
    from apps.jobs.jobs import daily_snapshot

    now = djtz.now()
    for i in range(3):
        _ad(catalog, f"stall{i:03d}", now - timedelta(hours=50))
    before = daily_snapshot()["ads"]

    _cover(now - timedelta(hours=2))
    mark_inactive()
    assert Ad.objects.filter(status=Ad.Status.UNVERIFIED).count() == 3

    assert daily_snapshot()["ads"] == before


@pytest.mark.django_db
def test_a_long_dead_listing_is_not_called_the_origin_of_a_new_one(catalog):
    """REPOST_WINDOW_DAYS has to bound both sides of the match.

    Windowing only the candidates left the predecessor search running over all
    history, so a matching spec from years ago would be linked to a new ad.
    """
    now = djtz.now()
    ancient = _ad(catalog, "old00001", now - timedelta(days=400))
    Ad.objects.filter(code="old00001").update(
        status=Ad.Status.REMOVED, listing_fingerprint="samecar",
        last_seen_at=now - timedelta(days=400),
    )
    fresh = _ad(catalog, "new00001", now)
    Ad.objects.filter(code="new00001").update(
        listing_fingerprint="samecar", first_seen_at=now - timedelta(days=1),
    )

    assert link_reposts()["linked"] == 0
    fresh.refresh_from_db()
    ancient.refresh_from_db()
    assert fresh.reposted_from_id is None
    assert ancient.likely_reason == ""


@pytest.mark.django_db
def test_pruned_counts_ads_not_cascaded_rows(known_catalog, make_payload):
    """`.delete()` returns every row it reached, which is not the ad count."""
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    observed = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    # A real ad, so it carries observations/versions/prices that CASCADE.
    _ing(extract_ad(make_payload("cascade1", 1_000_000_000), observed),
         run=run, observed_at=observed, publish_at=observed)
    # Strip the photo from the payload too, or the backfill simply refills it.
    Ad.objects.filter(code="cascade1").update(
        primary_image_url="", image_urls=[], raw_payload={"detail": {"code": "cascade1"}},
    )
    assert PriceObservation.objects.filter(ad__code="cascade1").exists(), "needs cascade rows"

    # 1 ad, not the handful of related rows deleted alongside it.
    assert backfill_images()["pruned"] == 1
