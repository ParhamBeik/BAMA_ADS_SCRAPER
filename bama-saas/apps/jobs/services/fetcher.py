"""Live Bama fetcher: stream ads straight from bama.ir into Postgres.

Small, proven HTTP helpers for the bama.ir listing API
(``create_session``, ``warmup``, ``fetch_page``, ``iter_ads``, the request
headers, the ``ad.get("type") != "banner"`` filter, and the
``SEARCH_URL`` / ``WARMUP_URL`` constants) inline rather than importing them,
so the SaaS stays decoupled from the scraper package.

Unlike the scraper, this does not write JSON files or touch SQLite. Each raw
ad dict is fed through the same pipeline as ``import_scraped``:
``extract_ad`` -> ``parse_publish_time`` -> ``ingest_ad``, so the per-ad
upsert, versioning, observation, price-change and dimension logic is reused
verbatim.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Iterator

import requests
from django.conf import settings
from django.utils import timezone as djtz

from apps.core.models import FetchRun, UnknownTimePhrase
from apps.jobs.services.dimensions import reset_cache
from apps.jobs.services.ingest import ingest_ad
from apps.parsing import extract_ad, parse_publish_time

# Constants and headers matching what bama.ir's own frontend sends.
SEARCH_URL = "https://bama.ir/cad/api/search"
WARMUP_URL = "https://bama.ir/car?image=1&priced=1"

HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fa,en;q=0.9",
    "Referer": "https://bama.ir/car",
    "X-Requested-With": "XMLHttpRequest",
}

# Persist the run row every N ads to bound progress loss on a long run.
SAVE_EVERY = 500


def create_session(cookie: str | None = None) -> requests.Session:
    """Build a requests.Session with the Bama headers (plus Cookie if given)."""
    session = requests.Session()
    headers = dict(HEADERS)
    if cookie:
        headers["Cookie"] = cookie
    session.headers.update(headers)
    return session


def warmup(session: requests.Session, request_timeout: int) -> None:
    """Prime cookies before the first API call; ignore network errors."""
    try:
        session.get(WARMUP_URL, timeout=request_timeout)
    except requests.RequestException:
        pass


def fetch_page(
    session: requests.Session, page: int, request_timeout: int
) -> list[dict[str, Any]]:
    """Fetch one page and drop banner rows (``ad.get("type") == "banner"``)."""
    response = session.get(
        f"{SEARCH_URL}?pageIndex={page}", timeout=request_timeout
    )
    response.raise_for_status()
    ads = response.json().get("data", {}).get("ads", [])
    return [ad for ad in ads if isinstance(ad, dict) and ad.get("type") != "banner"]


def iter_ads(
    session: requests.Session,
    *,
    max_ads: int,
    page_pause: float,
    request_timeout: int,
) -> Iterator[tuple[dict[str, Any], int]]:
    """Yield non-banner ads page by page until the limit or feed end."""
    page = 1
    yielded = 0
    while yielded < max_ads:
        ads = fetch_page(session, page, request_timeout)
        if not ads:
            break
        for ad in ads:
            if yielded >= max_ads:
                break
            yield ad, page
            yielded += 1
        page += 1
        time.sleep(page_pause)


def fetch_live(
    *,
    max_ads: int | None = None,
    page_pause: float | None = None,
    request_timeout: int | None = None,
) -> FetchRun:
    """Stream live Bama ads into Postgres via the shared ingest pipeline.

    Returns the persisted :class:`FetchRun`. ``KeyboardInterrupt`` flushes the
    run as ``SUCCEEDED`` (the partial state is already in Postgres); any other
    exception marks it ``FAILED`` with ``run.error``. ``finished_at`` is always
    set and the row saved in ``finally``.
    """
    max_ads = int(max_ads if max_ads is not None else settings.BAMA_MAX_ADS)
    page_pause = float(
        page_pause if page_pause is not None else settings.BAMA_PAGE_PAUSE
    )
    request_timeout = int(
        request_timeout
        if request_timeout is not None
        else settings.BAMA_REQUEST_TIMEOUT
    )

    run = FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH,
        status=FetchRun.Status.RUNNING,
        started_at=djtz.now(),
        max_ads=max_ads,
        page_pause=page_pause,
    )

    reset_cache()

    def record_unknown(phrase: str) -> None:
        obj, created = UnknownTimePhrase.objects.get_or_create(phrase=phrase)
        if not created:
            obj.seen_count = (obj.seen_count or 0) + 1
            obj.save(update_fields=["seen_count", "last_seen_at"])
        obj.last_fetch_run = run
        obj.save(update_fields=["last_fetch_run"])

    interrupted = False
    try:
        try:
            session = create_session(settings.BAMA_COOKIE or None)
            warmup(session, request_timeout)
            for ad, _page in iter_ads(
                session,
                max_ads=max_ads,
                page_pause=page_pause,
                request_timeout=request_timeout,
            ):
                observed_at = datetime.now(timezone.utc)
                extracted = extract_ad(ad, observed_at)
                if not extracted:
                    run.skipped_count += 1
                    continue
                publish_at = parse_publish_time(
                    extracted.get("publish_phrase"),
                    observed_at,
                    on_unknown=record_unknown,
                )
                _, created, price_changed = ingest_ad(
                    extracted,
                    run=run,
                    observed_at=observed_at,
                    publish_at=publish_at,
                    dealer=ad.get("dealer"),
                )
                run.fetched_count += 1
                if created:
                    run.created_count += 1
                else:
                    run.updated_count += 1
                if price_changed:
                    run.price_change_count += 1
                if run.fetched_count and run.fetched_count % SAVE_EVERY == 0:
                    run.save()
        except KeyboardInterrupt:
            # Partial state is already in Postgres; flush as SUCCEEDED.
            interrupted = True

        run.status = FetchRun.Status.SUCCEEDED
        if interrupted:
            run.error = run.error or "interrupted"
    except Exception as exc:  # noqa: BLE001
        run.status = FetchRun.Status.FAILED
        run.error = str(exc)[:4000]
        run.finished_at = djtz.now()
        run.save()
        reset_cache()
        raise
    finally:
        run.finished_at = djtz.now()
        run.save()
        reset_cache()

    return run
