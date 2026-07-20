"""Per-ad ingestion: upsert the snapshot row, immutable version, observation,
and change-only price observation. Idempotent — re-importing the same data
leaves Ad / AdVersion / PriceObservation counts unchanged (a new FetchRun each
run adds AdObservation provenance rows, which is expected).

Ported from ``bama-saas/app/services/ingestion.py``'s upsert flow, but using the
Django ORM.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from apps.catalog.models import Ad
from apps.history.models import AdChangeEvent, AdObservation, AdVersion, FetchRun
from apps.market.models import PriceDropEvent, PriceObservation
from apps.parsing import categories_for, diff_payloads, fingerprint, payload_hashes, pure_ad

from .dimensions import resolve_dimensions

# In-memory cache of the most-recent price fingerprint per ad code, so the
# change-only check is O(1) for history replay (each ad is observed many times).
# Falls back to one DB query on first sight of a code.
_PRICE_FP_CACHE: dict[str, str] = {}


def reset_price_cache() -> None:
    _PRICE_FP_CACHE.clear()


def _ad_defaults(extracted: dict, dims: dict, observed_at: datetime, publish_at) -> dict:
    g = extracted.get
    return {
        "brand": dims["brand"],
        "model": dims["model"],
        "variant": dims["variant"],
        "city": dims["city"],
        "dealer": dims["dealer"],
        "title": g("title") or "",
        "year": g("year"),
        "mileage": g("mileage"),
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
        # Being observed now means the ad is active: clear any prior removal
        # the worker may have recorded during a stale gap.
        "status": Ad.Status.ACTIVE,
        "removed_at": None,
        "raw_payload": pure_ad(g("raw_payload") or {}),
    }


def ingest_ad(
    extracted: dict,
    *,
    run: FetchRun,
    observed_at: datetime,
    publish_at,
    dealer: dict | None = None,
) -> tuple[Ad, bool, bool]:
    """Persist one extracted ad. Returns ``(ad, created, price_changed)``."""
    payload = extracted.get("raw_payload") or {}
    dims = resolve_dimensions(
        brand_name=extracted.get("brand"),
        model_name=extracted.get("model"),
        trim_name=extracted.get("trim"),
        city_location=extracted.get("location"),
        dealer=dealer,
    )
    defaults = _ad_defaults(extracted, dims, observed_at, publish_at)
    code = extracted["code"]

    # 1) Upsert the snapshot row. create() sets first_seen_at; update() is a
    # single SQL UPDATE without reloading the row.
    ad = Ad.objects.filter(code=code).first()
    if ad is None:
        ad = Ad(code=code, first_seen_at=observed_at, **defaults)
        ad.save()
        created = True
    else:
        Ad.objects.filter(code=code).update(**defaults)
        created = False

    # 2) Immutable version deduped by semantic hash.
    raw_hash, semantic_hash = payload_hashes(payload)
    version, v_created = AdVersion.objects.get_or_create(
        ad=ad,
        semantic_hash=semantic_hash,
        defaults={
            "raw_hash": raw_hash,
            "payload": pure_ad(payload),
            "origin": run.source,
            "first_observed_at": observed_at,
        },
    )

    # 3) One observation per (run, ad). Capture it for the change-event below.
    this_obs, _ = AdObservation.objects.get_or_create(
        fetch_run=run,
        ad=ad,
        defaults={
            "version": version,
            "observed_at": observed_at,
            "raw_hash": raw_hash,
            "publish_phrase": extracted.get("publish_phrase") or "",
        },
    )

    # 4) Change-only price: append only when the price fingerprint changes vs
    # the ad's immediately-preceding observation.
    price_fp = fingerprint(payload.get("price") or {})
    last_fp = _PRICE_FP_CACHE.get(code)
    old_price = None
    if last_fp is None:
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
        )
        _PRICE_FP_CACHE[code] = price_fp
        # A genuine price cut vs the prior observation → record a drop event.
        # Idempotent: only fires when a new PriceObservation is written, and
        # re-importing unchanged data writes no new observation.
        if old_price and new_price is not None and new_price < old_price:
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

    return ad, created, price_changed
