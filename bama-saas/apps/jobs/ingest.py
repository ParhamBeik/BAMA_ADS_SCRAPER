"""Persist one ad: dimensions, snapshot upsert, version, observation, price.

Idempotent — re-ingesting the same data leaves Ad / AdVersion /
PriceObservation counts unchanged. (A new FetchRun each run adds AdObservation
provenance rows, which is the point of them.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone
from typing import Any
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.utils.text import slugify

from apps.core.models import (
    Ad, AdObservation, AdVersion, Brand, City, Dealer, FetchRun, IngestReject,
    Model, PriceDropEvent, PriceObservation, Variant,
)
from apps.jobs.parsing import (
    SEMANTIC_HASH_VERSION, fingerprint, normalize_model_year, parse_int,
    parse_mileage, payload_hashes, pure_ad,
)
from apps.jobs.verify import verify_extracted

# ---------------------------------------------------------------------------
# Dimension resolution
# ---------------------------------------------------------------------------
#
# A process-local cache so a bulk import of ~44k ads does O(unique values)
# lookups instead of O(ads). `reset_cache` at the start and end of every run.

# Location strings look like "تهران - منطقه ۱" or "بابل"; the first segment is
# the city.
_LOCATION_SEP = re.compile(r"\s*[-–،,]\s*")

_DIM_CACHE: dict = {}


def reset_cache() -> None:
    _DIM_CACHE.clear()


def _brand(name: str | None) -> tuple[Any, bool]:
    """Resolve a brand. Second element is True when this call minted it."""
    if not name:
        return None, False
    name = name.strip()
    key = ("brand", name)
    if key in _DIM_CACHE:
        return _DIM_CACHE[key], False  # whoever minted it reported that then
    brand = Brand.objects.filter(name_fa=name).first()
    minted = False
    if brand is None:
        slug = slugify(name, allow_unicode=True) or name
        minted = True
        try:
            brand = Brand.objects.create(name_fa=name, slug=slug)
        except IntegrityError:
            # Slug collided with a different brand name; keep the name unique.
            existing = Brand.objects.filter(name_fa=name).first()
            if existing is not None:
                brand, minted = existing, False
            else:
                brand = Brand.objects.create(name_fa=name, slug=f"{slug}-{name[:40]}")
    _DIM_CACHE[key] = brand
    return brand, minted


def _model(brand, name: str | None) -> tuple[Any, bool]:
    if not brand or not name:
        return None, False
    name = name.strip()
    key = ("model", brand.pk, name)
    if key in _DIM_CACHE:
        return _DIM_CACHE[key], False
    model, minted = Model.objects.get_or_create(brand=brand, name_fa=name)
    _DIM_CACHE[key] = model
    return model, minted


def _variant(model, name: str | None):
    if not model:
        return None
    name = (name or "default").strip() or "default"
    key = ("variant", model.pk, name)
    if key not in _DIM_CACHE:
        _DIM_CACHE[key] = Variant.objects.get_or_create(model=model, name_fa=name)[0]
    return _DIM_CACHE[key]


def _city(location: str | None):
    if not location:
        return None
    first = _LOCATION_SEP.split(location.strip())[0].strip()
    if not first:
        return None
    key = ("city", first)
    if key in _DIM_CACHE:
        return _DIM_CACHE[key]
    try:
        city, _ = City.objects.get_or_create(name_fa=first)
    except IntegrityError:
        city = City.objects.filter(name_fa=first).first()  # concurrent mint
        if city is None:
            raise
    _DIM_CACHE[key] = city
    return city


def _dealer(data: dict | None):
    if not data or not data.get("id"):
        return None
    try:
        dealer_id = int(data["id"])
    except (TypeError, ValueError):
        return None
    key = ("dealer", dealer_id)
    if key not in _DIM_CACHE:
        _DIM_CACHE[key] = Dealer.objects.get_or_create(
            id=dealer_id,
            defaults={
                "name": data.get("name") or "",
                "type": data.get("type") or "",
                "package_type": data.get("package_type") or "",
                "score": data.get("score"),
                "ad_count": data.get("ad_count"),
                "address": data.get("address") or "",
                "link": data.get("link") or "",
                "logo": data.get("logo") or "",
            },
        )[0]
    return _DIM_CACHE[key]


def resolve_dimensions(*, brand_name, model_name, trim_name, city_location, dealer=None) -> dict:
    """Resolve every dimension for one ad.

    ``minted`` names the levels this ad brought into existence, so a Bama title
    format change surfaces as a spike in one place rather than silently growing
    the catalog.
    """
    brand, brand_minted = _brand(brand_name)
    model, model_minted = _model(brand, model_name)
    return {
        "brand": brand,
        "model": model,
        "variant": _variant(model, trim_name),
        "city": _city(city_location),
        "dealer": _dealer(dealer),
        "minted": [n for n, m in (("brand", brand_minted), ("model", model_minted)) if m],
    }


# ---------------------------------------------------------------------------
# Per-ad caches
# ---------------------------------------------------------------------------
#
# The most-recent (price fingerprint, price) per ad code, so the change-only
# check is O(1) when an ad is observed many times in one process. The *price* is
# cached alongside the fingerprint because PriceDropEvent needs the previous
# one: caching only the fingerprint left old_price as None and any second
# sighting of an ad inside one run silently lost its price cut.
_PRICE_FP_CACHE: dict[str, tuple[str, int | None]] = {}
_VERSION_CACHE: dict[tuple[str, str], AdVersion] = {}

# A price cut this large between two sightings is a unit switch (rials are 10x
# tomans), not a bargain. A PriceDropEvent is user-visible, and a rial->toman
# switch reads as a 90% cut that would top the deal board.
PRICE_DROP_SANITY_FACTOR = 3.0

# Bama sends modified_date as a bare local timestamp with no offset. Reading it
# as UTC would shift every value by Tehran's offset.
_SOURCE_TZ = ZoneInfo("Asia/Tehran")

_CDN_HOSTS = ("cdn.bama.ir", "bama.ir", "media.bama.ir")
_MAX_GALLERY = 12


def reset_price_cache() -> None:
    _PRICE_FP_CACHE.clear()
    _VERSION_CACHE.clear()


def forget_cached(code: str) -> None:
    """Drop one ad's cached version/price state after a rollback.

    Both caches hold Python objects that outlive the transaction that created
    them. When a write is rolled back, ``_VERSION_CACHE`` still holds an
    AdVersion whose row no longer exists, and the next sighting of that
    (code, semantic_hash) reuses it — inserting an AdObservation pointing at a
    version id Postgres never kept, which takes a whole run down.
    """
    _PRICE_FP_CACHE.pop(code, None)
    for key in [k for k in _VERSION_CACHE if k[0] == code]:
        del _VERSION_CACHE[key]


@dataclass(frozen=True)
class IngestResult:
    """What one ingest actually did."""

    ad: Ad | None
    created: bool = False
    price_changed: bool = False
    version_created: bool = False
    rejected: bool = False
    flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def cohort(self) -> tuple[int, int | None, int | None] | None:
        """The (model, variant, year) cohort key, or None when unusable."""
        if self.ad is None or self.ad.model_id is None:
            return None
        return (self.ad.model_id, self.ad.variant_id, self.ad.year_jalali)

    @property
    def changed(self) -> bool:
        """Did this observation carry anything new at all?

        The delta stop condition. Price is not the only thing that changes: a
        seller rewriting a description produces a new semantic version, and
        counting only new ads and price moves read that page as stale.
        """
        return self.created or self.price_changed or self.version_created


def parse_source_datetime(raw) -> datetime | None:
    """The source's own timestamp, localised from Tehran, or None."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_SOURCE_TZ)
    return parsed.astimezone(dt_timezone.utc)


def _image_urls(detail: dict) -> tuple[str, list[str]]:
    """HTTPS Bama-CDN image URLs only; gallery capped."""
    candidates: list[str] = []
    for key in ("images", "image", "media"):
        raw = detail.get(key)
        if isinstance(raw, str):
            candidates.append(raw)
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    candidates.append(item)
                elif isinstance(item, dict):
                    for k in ("url", "large", "original", "src"):
                        if isinstance(item.get(k), str):
                            candidates.append(item[k])
                            break
    urls: list[str] = []
    for u in candidates:
        u = u.strip()
        if not u.startswith("https://"):
            continue
        host = u.split("/")[2].lower()
        if not any(host == h or host.endswith("." + h) for h in _CDN_HOSTS):
            continue
        if u not in urls:
            urls.append(u)
        if len(urls) >= _MAX_GALLERY:
            break
    return (urls[0] if urls else ""), urls


def _ad_defaults(extracted: dict, dims: dict, observed_at, publish_at, quality_flags: list) -> dict:
    g = extracted.get
    detail = (g("raw_payload") or {}).get("detail") or {}
    # Normalise the model year once, here, where every ingest path funnels.
    year_jalali, year_gregorian, year_calendar = normalize_model_year(
        detail.get("year", g("year"))
    )
    # parse_mileage, unlike parse_int(positive=True), keeps a genuine 0 for
    # "صفر کیلومتر" instead of collapsing every brand-new car to NULL.
    mileage = parse_mileage(detail.get("mileage"))
    primary, gallery = _image_urls(detail)
    authenticated = detail.get("authenticated")
    if isinstance(authenticated, str):
        authenticated = authenticated.strip().lower() in {"true", "1", "yes"}
    description = detail.get("description")
    description = description.strip()[:8000] if isinstance(description, str) else ""
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
        "image_count": parse_int(detail.get("image_count")) or (len(gallery) or None),
        "description": description,
        "primary_image_url": primary[:500],
        "image_urls": gallery,
        "description_length": len(description) or None,
        "seller_authenticated": authenticated if isinstance(authenticated, bool) else None,
        "source_modified_at": parse_source_datetime(detail.get("modified_date")),
        # Being observed now means the ad is active: clear any prior removal.
        "status": Ad.Status.ACTIVE,
        "removed_at": None,
        "raw_payload": pure_ad(g("raw_payload") or {}),
        # Recomputed every observation, so a row quarantined by a bad payload
        # clears itself the moment Bama sends a good one.
        "quality_flags": quality_flags,
    }


def ingest_ad(extracted: dict, *, run: FetchRun, observed_at: datetime,
              publish_at, dealer: dict | None = None, rank: int | None = None) -> IngestResult:
    """Persist one ad in its own savepoint, quarantining it if the DB rejects it.

    Callers ingest a whole page inside one transaction, so without this an ad
    violating a constraint rolled back every other ad on its page *and* failed
    the run — one bad payload out of ~30 ending a 20-minute sweep. A nested
    ``atomic()`` is a savepoint: rolling back to it discards only this ad's
    writes. The quarantine row is written after the rollback so the evidence
    survives and a wrong rule stays replayable.
    """
    code = (extracted.get("code") or "")[:16]
    try:
        with transaction.atomic():
            return _ingest_ad(extracted, run=run, observed_at=observed_at,
                              publish_at=publish_at, dealer=dealer, rank=rank)
    except IntegrityError as exc:
        # The savepoint is gone, so anything cached from inside it is a lie.
        forget_cached(code)
        IngestReject.objects.create(
            code=code, rule="integrity_error", detail=str(exc)[:1000],
            raw_payload=pure_ad(extracted.get("raw_payload") or {}),
            fetch_run=run, observed_at=observed_at,
        )
        return IngestResult(ad=None, rejected=True, flags=("integrity_error",))


def _ingest_ad(extracted, *, run, observed_at, publish_at, dealer=None, rank=None) -> IngestResult:
    payload = extracted.get("raw_payload") or {}

    rejections = verify_extracted(extracted, payload)
    quality_flags = [r.rule for r in rejections]
    hard = [r for r in rejections if r.hard]

    if hard:
        code = (extracted.get("code") or "")[:16]
        IngestReject.objects.bulk_create([
            IngestReject(code=code, rule=r.rule, detail=r.detail,
                         raw_payload=pure_ad(payload), fetch_run=run,
                         observed_at=observed_at)
            for r in hard
        ])
        # An ad that was clean and has now gone bad must not linger as a stale
        # good-looking row — but deleting it would CASCADE away its versions,
        # observations and prices. Appending the hard rules to quality_flags
        # makes verified() exclude it while keeping the history.
        if code:
            ad = Ad.objects.filter(code=code).first()
            if ad:
                ad.quality_flags = list(set(ad.quality_flags or []) | set(quality_flags))
                ad.save()
        return IngestResult(ad=None, rejected=True, flags=tuple(quality_flags))

    dims = resolve_dimensions(
        brand_name=extracted.get("brand"), model_name=extracted.get("model"),
        trim_name=extracted.get("trim"), city_location=extracted.get("location"),
        dealer=dealer,
    )
    # A dimension that did not exist a moment ago is either a genuinely new car
    # or a parse failure inventing catalog rows. Soft: the ad is fine, it is the
    # *catalog* that is now unproven.
    if dims["minted"]:
        quality_flags = [*quality_flags, "unknown_dimension"]

    code = extracted["code"]
    # The stored row *before* this observation. Loaded here because the
    # price-drop check compares against it, and the UPDATE below destroys it.
    ad = Ad.objects.filter(code=code).first()
    defaults = _ad_defaults(extracted, dims, observed_at, publish_at, quality_flags)

    # 1) Upsert the snapshot row.
    if ad is None:
        ad = Ad(code=code, first_seen_at=observed_at, **defaults)
        ad.save()
        created = True
    else:
        # Keep the lifecycle bounds monotonic. `observed_at` is NOT increasing
        # across calls — backfill and gap repair refetch pages out of order — so
        # the unconditional last_seen_at above dragged it *backwards*. 5,009 ads
        # ended up with last_seen_at < first_seen_at, making every time-on-feed
        # duration negative.
        if ad.last_seen_at and ad.last_seen_at > observed_at:
            defaults["last_seen_at"] = ad.last_seen_at
            # A stale sighting is not evidence the ad is live now, so it must
            # not resurrect a REMOVED row using page data older than the removal
            # decision.
            defaults.pop("status", None)
            defaults.pop("removed_at", None)
        if ad.first_seen_at and observed_at < ad.first_seen_at:
            defaults["first_seen_at"] = observed_at
        Ad.objects.filter(code=code).update(**defaults)
        created = False

    # 2) Immutable version, deduped by semantic hash.
    raw_hash, semantic_hash = payload_hashes(payload)
    version_key = (code, semantic_hash)
    version = _VERSION_CACHE.get(version_key)
    version_created = False
    if version is None:
        version, version_created = AdVersion.objects.get_or_create(
            ad=ad, semantic_hash=semantic_hash,
            defaults={
                "raw_hash": raw_hash,
                "semantic_hash_version": SEMANTIC_HASH_VERSION,
                "payload": pure_ad(payload),
                "origin": run.source if run else AdVersion.Origin.BULK_IMPORT,
                "first_observed_at": observed_at,
            },
        )
        _VERSION_CACHE[version_key] = version

    # 3) One observation per (run, ad). History replay knows the pair is unique,
    # so it can skip get_or_create's SELECT.
    observation_fields = {
        "version": version, "observed_at": observed_at, "raw_hash": raw_hash,
        "rank": rank, "publish_phrase": extracted.get("publish_phrase") or "",
    }
    if run and run.source == FetchRun.Source.HISTORY_REPLAY:
        AdObservation.objects.create(fetch_run=run, ad=ad, **observation_fields)
    else:
        AdObservation.objects.get_or_create(fetch_run=run, ad=ad, defaults=observation_fields)

    # 4) Change-only price: append only when the fingerprint differs from the
    # ad's immediately-preceding observation.
    price_fp = fingerprint(payload.get("price") or {})
    cached = _PRICE_FP_CACHE.get(code)
    if cached is not None:
        last_fp, old_price = cached
    else:
        latest = PriceObservation.objects.filter(ad=ad).order_by("-observed_at", "-id").first()
        last_fp = latest.fingerprint if latest else None
        old_price = latest.price if latest else None

    price_changed = last_fp != price_fp
    new_price = extracted.get("current_price")
    if price_changed:
        PriceObservation.objects.create(
            ad=ad, fetch_run=run, observed_at=observed_at, price=new_price,
            payment=extracted.get("current_payment"),
            prepayment=extracted.get("current_prepayment"),
            installments=extracted.get("current_installments"),
            price_type=extracted.get("price_type") or "", fingerprint=price_fp,
        )
        _PRICE_FP_CACHE[code] = (price_fp, new_price)
        # Idempotent: only fires when a new PriceObservation is written, and
        # re-importing unchanged data writes none.
        if (old_price and new_price is not None and new_price < old_price
                and new_price >= old_price / PRICE_DROP_SANITY_FACTOR):
            PriceDropEvent.objects.create(
                ad=ad, old_price=old_price, new_price=new_price,
                drop_amount=old_price - new_price,
                drop_pct=round((old_price - new_price) / old_price * 100, 2),
                observed_at=observed_at,
            )

    return IngestResult(ad=ad, created=created, price_changed=price_changed,
                        version_created=version_created, flags=tuple(quality_flags))
