"""Per-ad ingestion: upsert the snapshot row, immutable version, observation,
and change-only price observation. Idempotent — re-importing the same data
leaves Ad / AdVersion / PriceObservation counts unchanged (a new FetchRun each
run adds AdObservation provenance rows, which is expected).

Ported from ``bama-saas/app/services/ingestion.py``'s upsert flow, but using the
Django ORM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone
from typing import Any
from zoneinfo import ZoneInfo

from apps.core.models import Ad
from apps.core.models import AdChangeEvent, AdObservation, AdVersion, FetchRun
from apps.core.models import IngestReject, PriceDropEvent, PriceObservation
from apps.parsing import (
    SEMANTIC_HASH_VERSION,
    categories_for,
    diff_payloads,
    fingerprint,
    normalize_model_year,
    parse_int,
    parse_mileage,
    payload_hashes,
    pure_ad,
)

from .dimensions import resolve_dimensions
from .verify import verify_extracted
from .verify_temporal import verify_against_previous

# In-memory cache of the most-recent (price fingerprint, price) per ad code, so
# the change-only check is O(1) for history replay (each ad is observed many
# times). Falls back to one DB query on first sight of a code.
#
# The price is cached alongside the fingerprint because the PriceDropEvent below
# needs the previous price: when only the fingerprint was cached, a warm cache
# left old_price as None and the drop event silently never fired — so any second
# sighting of an ad inside one process (history replay, a gap refetch landing on a
# page already read this run) lost its price cut.
_PRICE_FP_CACHE: dict[str, tuple[str, int | None]] = {}
_VERSION_CACHE: dict[tuple[str, str], AdVersion] = {}

# Temporal flags that describe the price transition itself, so they belong on the
# PriceObservation rather than only on the Ad.
_PRICE_TRANSITION_FLAGS = frozenset({"price_jump"})


@dataclass(frozen=True)
class IngestResult:
    """What one ingest actually did.

    Replaced a ``(ad, created, price_changed)`` tuple. Two callers needed facts
    the tuple could not carry: the delta fetcher has to know whether a *new
    version* appeared (a page of description edits is not a stale page), and the
    cohort outlier pass has to know which cohorts a fetch touched so it can
    rescore those instead of the whole market.
    """

    ad: Ad | None
    created: bool = False
    price_changed: bool = False
    version_created: bool = False
    rejected: bool = False
    flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def cohort(self) -> tuple[int, int | None, int | None] | None:
        """The ad's (model, variant, year) cohort key, or None when unusable."""
        if self.ad is None or self.ad.model_id is None:
            return None
        return (self.ad.model_id, self.ad.variant_id, self.ad.year_jalali)

    @property
    def changed(self) -> bool:
        """Did this observation carry anything new at all?

        The delta stop condition. Price is not the only thing that changes: a
        seller rewriting a description or adding photos produces a new semantic
        version, and counting only new ads and price moves read that page as
        stale and stopped the crawl one page early.
        """
        return self.created or self.price_changed or self.version_created


def reset_price_cache() -> None:
    _PRICE_FP_CACHE.clear()
    _VERSION_CACHE.clear()


# Bama sends modified_date as a bare local timestamp ("2026-05-13T12:30:58.32")
# with no offset. Reading it as UTC would silently shift every value by Tehran's
# offset, so it is localised to Tehran and converted — an assumption, but a
# defensible one, and far better than an off-by-3.5-hours that never surfaces.
_SOURCE_TZ = ZoneInfo("Asia/Tehran")


def parse_source_datetime(raw) -> datetime | None:
    """Parse the source's own timestamp, or None if it is missing or malformed."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_SOURCE_TZ)
    return parsed.astimezone(dt_timezone.utc)


def _presentation_fields(detail: dict) -> dict:
    """Promote the listing-presentation fields already present in the payload.

    Kept tolerant: every value here is optional and a missing or malformed one
    must never cost us the ad. ``None`` means "not stated", which is a different
    fact from zero and has to stay distinguishable.
    """
    description = detail.get("description")
    authenticated = detail.get("authenticated")
    if isinstance(authenticated, str):
        authenticated = authenticated.strip().lower() in {"true", "1", "yes"}
    return {
        "image_count": parse_int(detail.get("image_count"), positive=False),
        "description_length": len(description) if isinstance(description, str) else None,
        "seller_authenticated": authenticated if isinstance(authenticated, bool) else None,
        "source_modified_at": parse_source_datetime(detail.get("modified_date")),
    }


def _ad_defaults(
    extracted: dict, dims: dict, observed_at: datetime, publish_at, quality_flags: list
) -> dict:
    g = extracted.get
    detail = (g("raw_payload") or {}).get("detail") or {}
    # Bama sends the model year in either calendar depending on the brand, so the
    # raw value is unusable as a cohort key. Normalize once, here, where every
    # ingest path (live fetch, bulk import, history replay) funnels through.
    year_jalali, year_gregorian, year_calendar = normalize_model_year(
        detail.get("year", g("year"))
    )
    # parse_mileage, unlike parse_int(positive=True), keeps a genuine 0 for
    # "صفر کیلومتر" instead of collapsing every brand-new car to NULL.
    mileage = parse_mileage(detail.get("mileage"))
    return {
        "brand": dims["brand"],
        "model": dims["model"],
        "variant": dims["variant"],
        "city": dims["city"],
        "dealer": dims["dealer"],
        "title": g("title") or "",
        "year": g("year"),
        "year_jalali": year_jalali,
        "year_gregorian": year_gregorian,
        "year_calendar": year_calendar,
        # Fall back to the extractor's value only when the payload had no
        # mileage at all, so a missing payload never wipes a known number.
        "mileage": mileage if mileage is not None else g("mileage"),
        "category": g("category") or "",
        "transmission": g("transmission") or "",
        "current_price": g("current_price"),
        "current_payment": g("current_payment"),
        "current_prepayment": g("current_prepayment"),
        "current_installments": g("current_installments"),
        "price_type": g("price_type") or "",
        "publish_at": publish_at,
        "publish_phrase": g("publish_phrase") or "",
        "last_seen_at": observed_at,
        "trim": g("trim") or "",
        "location": g("location") or "",
        "body_type": g("body_type") or "",
        "body_color": g("body_color") or "",
        "body_status": g("body_status") or "",
        "fuel": g("fuel") or "",
        "url": g("url") or "",
        "canonical_path": (detail.get("url") or "")[:400],
        **_presentation_fields(detail),
        # Being observed now means the ad is active: clear any prior removal
        # the worker may have recorded during a stale gap.
        "status": Ad.Status.ACTIVE,
        "removed_at": None,
        "raw_payload": pure_ad(g("raw_payload") or {}),
        # Recomputed every observation, so a row that was quarantined by a bad
        # payload clears itself the moment Bama sends a good one.
        "quality_flags": quality_flags,
    }


def ingest_ad(
    extracted: dict,
    *,
    run: FetchRun,
    observed_at: datetime,
    publish_at,
    dealer: dict | None = None,
    rank: int | None = None,
) -> IngestResult:
    """Persist one extracted ad and report what changed.

    Returns a result with ``rejected=True`` and no ad when a hard rule fires:
    such a row is unusable and unrepairable (Bama itself sent the bad value), so
    it never reaches the Ad table. The payload is still kept in IngestReject, so
    the evidence survives and a rule that turns out to be wrong stays replayable.

    ``rank`` is the ad's position in the feed. It belongs on the observation, and
    passing it in costs nothing; the fetcher previously issued one extra UPDATE
    per ad to stamp it after the fact.
    """
    payload = extracted.get("raw_payload") or {}

    # Verify before persisting. Soft failures only flag the row; hard failures
    # quarantine the payload in IngestReject and keep the Ad table clean.
    rejections = verify_extracted(extracted, payload)
    quality_flags = [r.rule for r in rejections]
    hard = [r for r in rejections if r.hard]

    if hard:
        code = (extracted.get("code") or "")[:16]
        IngestReject.objects.bulk_create(
            [
                IngestReject(
                    code=code,
                    rule=r.rule,
                    detail=r.detail,
                    raw_payload=pure_ad(payload),
                    fetch_run=run,
                    observed_at=observed_at,
                )
                for r in hard
            ]
        )
        # An ad that was clean before and has now gone bad must not linger as a
        # stale good-looking row, but we must not delete it and cause CASCADE
        # data loss (versions/observations/prices). Instead, append the hard
        # rules to its quality_flags so verified() automatically excludes it.
        if code:
            ad = Ad.objects.filter(code=code).first()
            if ad:
                current_flags = set(ad.quality_flags or [])
                current_flags.update(quality_flags)
                ad.quality_flags = list(current_flags)
                ad.save()
        return IngestResult(ad=None, rejected=True, flags=tuple(quality_flags))

    dims = resolve_dimensions(
        brand_name=extracted.get("brand"),
        model_name=extracted.get("model"),
        trim_name=extracted.get("trim"),
        city_location=extracted.get("location"),
        dealer=dealer,
    )
    # A dimension that did not exist a moment ago is either a genuinely new car or
    # a parse failure inventing catalog rows, and both deserve a look. Soft flag:
    # the ad itself is fine, it is the *catalog* that is now unproven.
    if dims["minted"]:
        quality_flags = [*quality_flags, "unknown_dimension"]

    code = extracted["code"]

    # The stored row *before* this observation is applied. Loaded here rather than
    # inside the upsert because the temporal rules below compare against it, and
    # once the UPDATE runs the previous values are gone.
    ad = Ad.objects.filter(code=code).first()

    # Rules that only a pair of sightings can express: unit switches, odometer
    # rollbacks, a recycled listing code. All soft — an impossible transition
    # proves one of the two observations is wrong without saying which.
    temporal = verify_against_previous(extracted, payload, ad, dims)
    quality_flags = [*quality_flags, *(r.rule for r in temporal)]
    temporal_flags = {r.rule for r in temporal}

    defaults = _ad_defaults(extracted, dims, observed_at, publish_at, quality_flags)

    # 1) Upsert the snapshot row. create() sets first_seen_at; update() is a
    # single SQL UPDATE without reloading the row.
    if ad is None:
        ad = Ad(code=code, first_seen_at=observed_at, **defaults)
        ad.save()
        created = True
    else:
        # Keep the lifecycle bounds monotonic. `observed_at` is NOT
        # monotonically increasing across calls: import_history replays old
        # observations, and backfill/crawl_gaps refetch pages out of order. The
        # unconditional `last_seen_at = observed_at` in _ad_defaults therefore
        # dragged last_seen_at *backwards*, and first_seen_at was frozen at
        # whichever run happened to create the row rather than the earliest
        # sighting. 5,009 ads ended up with last_seen_at < first_seen_at, which
        # made every time-on-feed duration negative.
        stale_observation = bool(ad.last_seen_at and ad.last_seen_at > observed_at)
        if stale_observation:
            defaults["last_seen_at"] = ad.last_seen_at
            # A *stale* sighting is not evidence the ad is live now, so it must
            # not resurrect a REMOVED row. Without this, the first crawl_gaps
            # backfill after mark_inactive_ads would flip removed ads back to
            # ACTIVE using page data older than the removal decision.
            defaults.pop("status", None)
            defaults.pop("removed_at", None)
        if ad.first_seen_at and observed_at < ad.first_seen_at:
            defaults["first_seen_at"] = observed_at
        Ad.objects.filter(code=code).update(**defaults)
        created = False

    # 2) Immutable version deduped by semantic hash (with process-local caching).
    raw_hash, semantic_hash = payload_hashes(payload)
    version_key = (code, semantic_hash)
    version = _VERSION_CACHE.get(version_key)
    v_created = False
    if version is None:
        version, v_created = AdVersion.objects.get_or_create(
            ad=ad,
            semantic_hash=semantic_hash,
            defaults={
                "raw_hash": raw_hash,
                "semantic_hash_version": SEMANTIC_HASH_VERSION,
                "payload": pure_ad(payload),
                "origin": run.source if run else AdVersion.Origin.BULK_IMPORT,
                "first_observed_at": observed_at,
            },
        )
        _VERSION_CACHE[version_key] = version

    # 3) One observation per (run, ad). Capture it for the change-event below.
    # In HISTORY_REPLAY mode, we know the (run, ad) combination is unique, so we can skip
    # the SELECT of get_or_create and use create() directly.
    if run and run.source == FetchRun.Source.HISTORY_REPLAY:
        this_obs = AdObservation.objects.create(
            fetch_run=run,
            ad=ad,
            version=version,
            observed_at=observed_at,
            raw_hash=raw_hash,
            rank=rank,
            publish_phrase=extracted.get("publish_phrase") or "",
        )
    else:
        this_obs, _ = AdObservation.objects.get_or_create(
            fetch_run=run,
            ad=ad,
            defaults={
                "version": version,
                "observed_at": observed_at,
                "raw_hash": raw_hash,
                "rank": rank,
                "publish_phrase": extracted.get("publish_phrase") or "",
            },
        )

    # 4) Change-only price: append only when the price fingerprint changes vs
    # the ad's immediately-preceding observation.
    price_fp = fingerprint(payload.get("price") or {})
    cached = _PRICE_FP_CACHE.get(code)
    if cached is not None:
        last_fp, old_price = cached
    else:
        latest = (
            PriceObservation.objects.filter(ad=ad)
            .order_by("-observed_at", "-id")
            .first()
        )
        last_fp = latest.fingerprint if latest else None
        old_price = latest.price if latest else None
    price_changed = last_fp != price_fp
    new_price = extracted.get("current_price")
    if price_changed:
        PriceObservation.objects.create(
            ad=ad,
            fetch_run=run,
            observed_at=observed_at,
            price=new_price,
            payment=extracted.get("current_payment"),
            prepayment=extracted.get("current_prepayment"),
            installments=extracted.get("current_installments"),
            price_type=extracted.get("price_type") or "",
            fingerprint=price_fp,
            quality_flags=sorted(temporal_flags & _PRICE_TRANSITION_FLAGS),
        )
        _PRICE_FP_CACHE[code] = (price_fp, new_price)
        # A genuine price cut vs the prior observation → record a drop event.
        # Idempotent: only fires when a new PriceObservation is written, and
        # re-importing unchanged data writes no new observation.
        #
        # A flagged transition is excluded: a rial/toman unit switch reads as a
        # 90% cut, and a PriceDropEvent is user-visible — it would be published as
        # the best deal on the site.
        genuine_cut = (
            not temporal_flags & _PRICE_TRANSITION_FLAGS
            and old_price
            and new_price is not None
            and new_price < old_price
        )
        if genuine_cut:
            PriceDropEvent.objects.create(
                ad=ad,
                old_price=old_price,
                new_price=new_price,
                drop_amount=old_price - new_price,
                drop_pct=round((old_price - new_price) / old_price * 100, 2),
                observed_at=observed_at,
            )

    # 5) Content change events: only for genuinely new versions vs the previous
    # observation (drives the history timeline for re-imports / live fetch).
    # diff_payloads returns a list of {"path","before","after"} entries.
    if v_created:
        prev_obs = (
            AdObservation.objects.filter(ad=ad)
            .exclude(fetch_run=run)
            .order_by("-observed_at")
            .first()
        )
        if prev_obs and prev_obs.version_id != version.pk:
            changes = diff_payloads(prev_obs.version.payload or {}, pure_ad(payload))
            if changes:
                changed_paths = [c["path"] for c in changes]
                AdChangeEvent.objects.get_or_create(
                    observation=this_obs,
                    event_type=AdChangeEvent.EventType.CONTENT_CHANGED,
                    defaults={
                        "ad": ad,
                        "previous_version": prev_obs.version,
                        "new_version": version,
                        "categories": categories_for(changed_paths),
                        "changed_paths": changed_paths,
                        "changes": changes,
                        "origin": run.source,
                    },
                )

    return IngestResult(
        ad=ad,
        created=created,
        price_changed=price_changed,
        version_created=v_created,
        flags=tuple(quality_flags),
    )
