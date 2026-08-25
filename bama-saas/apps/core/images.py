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
still passes ``parsing.is_cdn_url``. That check is re-run here rather than
trusted from ingest: this endpoint turns a request parameter into an outbound
HTTP call, which is the one place in the app where a stored value being wrong
would become a server-side request to somewhere of an attacker's choosing. One
shared predicate, so the writer and the reader cannot disagree about which hosts
are allowed.
"""

from __future__ import annotations

import hashlib
import logging

import requests
from django.conf import settings
from django.core.cache import cache

from apps.jobs.fetcher import HEADERS
from apps.jobs.parsing import is_cdn_url

log = logging.getLogger("bama.images")

CACHE_PREFIX = "img:v1:"


def proxy_path(code: str, index: int) -> str:
    """Where the browser should ask for this ad's Nth gallery photo."""
    return f"/api/img/{code}/{index}/"


def thumb_path(code: str) -> str:
    """Where the browser should ask for this ad's card-sized photo.

    Its own address rather than gallery index 0, because the two are different
    files: ingest stores the CDN's ``resize,w_450`` variant in
    ``primary_image_url`` and the ``w_600`` one in ``image_urls``. Addressing
    both through one index served the large file to every card on a 24-card
    grid, which is the whole saving the two widths exist for.
    """
    return f"/api/img/{code}/thumb/"


def ad_image_paths(ad) -> tuple[str, list[str]]:
    """``(thumb_path, gallery_paths)`` for one ad, or empties when it has none.

    The thumbnail falls back to the gallery's first photo for rows that have a
    gallery but no stored thumbnail, so a listing with one photo and a listing
    with twelve are addressed the same way.
    """
    gallery = ad.image_urls or []
    if not (ad.primary_image_url or gallery):
        return "", []
    return thumb_path(ad.code), [proxy_path(ad.code, i) for i in range(len(gallery))]


def source_url(ad, index: int | None) -> str:
    """The CDN URL behind one address, re-validated against the host allowlist.

    ``index=None`` means the thumbnail; an integer addresses ``image_urls``.
    """
    gallery = ad.image_urls or []
    if index is None:
        url = ad.primary_image_url or (gallery[0] if gallery else "")
    elif 0 <= index < len(gallery):
        url = gallery[index]
    elif index == 0:
        url = ad.primary_image_url
    else:
        return ""
    return url if is_cdn_url(url) else ""


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
        # `with`, because both early returns below abandon a half-read stream:
        # while Bama is blocking us it answers every one of these with an HTML
        # error page, so the "not an image" branch is the common case exactly
        # when the connection matters, and leaving them unclosed drops sockets
        # instead of returning them to the pool.
        with requests.get(
            url,
            headers={**HEADERS, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"},
            timeout=settings.BAMA_REQUEST_TIMEOUT,
            stream=True,
        ) as response:
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
