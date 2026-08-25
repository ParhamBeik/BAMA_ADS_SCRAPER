"""Listing photos, served from our own origin instead of Bama's CDN.

Two problems, one answer. Bama's CDN 500s on a fraction of its own images and
periodically blocks our egress entirely (see ``fetcher``'s crawl gate) — either
way the user sees a grid of broken thumbnails. And a card page is ~24 images, so
hotlinking put 24 requests per page view onto a host that is already refusing
us.

Every photo is therefore fetched once, cached in Redis, and served from
``/api/img/<code>/<index>/`` with an immutable cache header. Bama's image URLs
are content-addressed (a GUID per upload), so a cached entry can never go stale
against a changed picture — the URL changes when the picture does.

The proxy will only ever fetch a URL that is already stored on the ad *and*
still passes the CDN host allowlist. That check is re-run here rather than
trusted from ingest: this endpoint turns a request parameter into an outbound
HTTP call, which is the one place in the app where a stored value being wrong
would become a server-side request to somewhere of an attacker's choosing.
"""

from __future__ import annotations

import hashlib
import logging

import requests
from django.conf import settings
from django.core.cache import cache

from apps.jobs.fetcher import HEADERS
from apps.jobs.ingest import _CDN_HOSTS

log = logging.getLogger("bama.images")

CACHE_PREFIX = "img:v1:"


def proxy_path(code: str, index: int) -> str:
    """Where the browser should ask for this ad's Nth photo."""
    return f"/api/img/{code}/{index}/"


def ad_image_paths(ad) -> tuple[str, list[str]]:
    """``(primary_path, gallery_paths)`` for one ad, or empties when it has none.

    Indexes address ``image_urls``; index 0 falls back to ``primary_image_url``
    for the rows whose gallery is still empty, so a listing with one photo and a
    listing with twelve are addressed the same way.
    """
    gallery = ad.image_urls or []
    if not gallery:
        return (proxy_path(ad.code, 0) if ad.primary_image_url else "", [])
    return proxy_path(ad.code, 0), [proxy_path(ad.code, i) for i in range(len(gallery))]


def source_url(ad, index: int) -> str:
    """The CDN URL behind one index, re-validated against the host allowlist."""
    gallery = ad.image_urls or []
    if index < len(gallery):
        url = gallery[index]
    elif index == 0:
        url = ad.primary_image_url
    else:
        return ""
    if not isinstance(url, str) or not url.startswith("https://"):
        return ""
    host = url.split("/")[2].lower()
    if not any(host == h or host.endswith("." + h) for h in _CDN_HOSTS):
        return ""
    return url


def fetch(url: str) -> tuple[str, bytes] | None:
    """``(content_type, body)`` from cache, or from the CDN once. None on failure.

    A miss that fails upstream is deliberately *not* cached as a negative: the
    usual cause is a block that lifts on Bama's schedule, and remembering the
    failure for a month would outlive the outage by a wide margin.
    """
    key = CACHE_PREFIX + hashlib.sha256(url.encode()).hexdigest()
    hit = cache.get(key)
    if hit is not None:
        return hit

    try:
        response = requests.get(
            url,
            headers={**HEADERS, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"},
            timeout=settings.BAMA_REQUEST_TIMEOUT,
            stream=True,
        )
        response.raise_for_status()
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
        if not content_type.startswith("image/"):
            log.warning("images: %s answered %s, not an image", url, content_type or "?")
            return None

        # Read with a ceiling rather than trusting Content-Length, which a
        # source is free to understate or omit.
        body = bytearray()
        for chunk in response.iter_content(64 * 1024):
            body.extend(chunk)
            if len(body) > settings.IMAGE_MAX_BYTES:
                log.warning("images: %s exceeded %d bytes", url, settings.IMAGE_MAX_BYTES)
                return None
    except requests.RequestException as exc:
        log.warning("images: %s failed: %s", url, exc)
        return None

    value = (content_type, bytes(body))
    cache.set(key, value, settings.IMAGE_CACHE_SECONDS)
    return value
