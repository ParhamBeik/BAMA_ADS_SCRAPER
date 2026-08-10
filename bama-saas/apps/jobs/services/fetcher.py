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

Feed facts this module encodes (verified against the live API):

* ``pageIndex`` is **0-based**. ``pageIndex=0`` is the newest ~30 ads; starting
  at 1 silently skips them on every single run.
* Each ad carries ``detail.rank``, its position in the recency-ordered feed,
  following ``rank ~= 30 * pageIndex + 1..30``.
* The feed ends naturally: the page past the last ad returns an empty list with
  HTTP 200. That empty page is the only proof of full coverage.
* Insertions push ads to higher ranks (a forward sweep just re-reads them,
  which is idempotent). Deletions pull ads to *lower* ranks, behind pages
  already read — the one case that silently loses ads, which is why every page
  writes a :class:`PageCoverage` row instead of trusting elapsed wall-clock.
"""

from __future__ import annotations

import math
import random
import signal
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterator

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone as djtz

from apps.core.models import FetchRun, PageCoverage, UnknownTimePhrase
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

# Feed geometry.
PAGE_SIZE = 30
FIRST_PAGE = 0

# Adaptive backoff on 429 / 5xx.
MAX_RETRIES = 3
BACKOFF_BASE = 1.0
BACKOFF_CAP = 30.0

# Jitter as a fraction of the computed delay. Without it every retry after a
# shared outage fires at the same instant, which is how a struggling server gets
# a synchronised second wave from everyone who backed off together.
BACKOFF_JITTER = 0.25

# An upper bound on a server-supplied Retry-After. The header is honoured because
# it is the server saying exactly how long it wants to be left alone, but a
# misconfigured or hostile value must not park a sweep for hours.
RETRY_AFTER_CAP = 120.0

# An empty page is indistinguishable from the end of the feed, so it is always
# confirmed with a second request after this pause before being believed.
EMPTY_PAGE_RECHECK_PAUSE = 2.0

# How much shallower than the last completed sweep an "end of feed" may be before
# it is disbelieved. The feed genuinely shrinks and grows, so this is deliberately
# generous: it is here to catch a truncated crawl, not to police market size.
#
# The failure it exists for is recorded: a sweep stopped at 266 pages against a
# feed that was ~1100 pages deep, stamped reached_end, and every downstream
# consumer — coverage, removal detection, the market index — then treated three
# quarters of the market as having vanished.
MIN_END_OF_FEED_DEPTH_RATIO = 0.5

# Delta depth floor. Measured churn is ~1.5 new ads/min; after downtime we scan
# at least deep enough to cover what landed while we were away instead of
# stopping on the first few stale pages.
CHURN_ADS_PER_MIN = 1.5
DELTA_FLOOR_MAX_PAGES = 40
DEFAULT_MAX_STALE_PAGES = 3


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


def _retryable(exc: Exception) -> bool:
    """429 and 5xx are worth waiting out; 4xx and malformed JSON are not."""
    if isinstance(exc, requests.HTTPError):
        status = getattr(exc.response, "status_code", None)
        return status == 429 or (status is not None and 500 <= status < 600)
    return isinstance(exc, requests.RequestException)


def retry_after_seconds(exc: Exception) -> float | None:
    """The server's own Retry-After, in seconds, when it sent a usable one.

    Both RFC forms are accepted: a delay in seconds, or an HTTP date. Values are
    capped — a server asking to be left alone for an hour should not silently
    park a sweep — and nonsense is ignored rather than raised on.
    """
    response = getattr(exc, "response", None)
    raw = (getattr(response, "headers", None) or {}).get("Retry-After")
    if not raw:
        return None
    raw = str(raw).strip()
    try:
        seconds = float(raw)
    except ValueError:
        try:
            # Raises on anything unparseable (it does not return None), and a
            # malformed header must never take down the retry path that exists to
            # survive a misbehaving server in the first place.
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        seconds = (parsed - datetime.now(timezone.utc)).total_seconds()
    if seconds < 0:
        return None
    return min(seconds, RETRY_AFTER_CAP)


def _backoff_delay(exc: Exception, attempt_delay: float) -> float:
    """How long to wait before the next attempt.

    Retry-After wins when present: it is the server stating its own terms, and
    guessing an exponential curve against an explicit instruction is how a client
    earns a ban. Jitter is applied either way so simultaneous clients do not
    resynchronise into a second thundering herd.
    """
    delay = retry_after_seconds(exc)
    if delay is None:
        delay = attempt_delay
    return delay * (1 + random.uniform(0, BACKOFF_JITTER))


def fetch_page_with_backoff(
    session: requests.Session, page: int, request_timeout: int
) -> list[dict[str, Any]]:
    """``fetch_page`` with Retry-After-aware jittered backoff. Re-raises when spent."""
    delay = BACKOFF_BASE
    for attempt in range(MAX_RETRIES + 1):
        try:
            return fetch_page(session, page, request_timeout)
        except Exception as exc:  # noqa: BLE001
            if attempt >= MAX_RETRIES or not _retryable(exc):
                raise
            time.sleep(_backoff_delay(exc, delay))
            delay = min(delay * 2, BACKOFF_CAP)
    raise AssertionError("unreachable")  # pragma: no cover


def rank_of(ad: dict[str, Any], page: int, offset: int) -> int:
    """The ad's feed rank, falling back to page arithmetic when absent."""
    try:
        return int((ad.get("detail") or {})["rank"])
    except (KeyError, TypeError, ValueError):
        return PAGE_SIZE * page + offset


def iter_pages(
    session: requests.Session,
    *,
    max_ads: int,
    page_pause: float,
    request_timeout: int,
    start_page: int = FIRST_PAGE,
    end_page: int | None = None,
) -> Iterator[tuple[int, list[tuple[dict[str, Any], int]]]]:
    """Yield ``(page_index, [(ad, rank), ...])`` page by page from ``start_page``.

    A trailing empty list means the feed ended there — the caller turns that
    into ``reached_end``. Stops silently on ``max_ads`` or ``end_page``.
    """
    page = start_page
    yielded = 0
    while yielded < max_ads and (end_page is None or page <= end_page):
        ads = fetch_page_with_backoff(session, page, request_timeout)
        if not ads:
            # A throttled or briefly-degraded API answers 200 with an empty ad
            # list, which is byte-for-byte identical to the end of the feed.
            # Believing it truncates the sweep *and* stamps reached_end, so the
            # tail is never revisited — and crawl_gaps cannot see the hole
            # either, because its ceiling is the deepest rank actually observed.
            # Confirming with one more request is the cheapest way to tell a
            # real end from a blip.
            time.sleep(EMPTY_PAGE_RECHECK_PAUSE)
            ads = fetch_page_with_backoff(session, page, request_timeout)
        if not ads:
            yield page, []
            return
        rows: list[tuple[dict[str, Any], int]] = []
        for offset, ad in enumerate(ads, start=1):
            if yielded >= max_ads:
                break
            rows.append((ad, rank_of(ad, page, offset)))
            yielded += 1
        yield page, rows
        page += 1
        time.sleep(page_pause)


def iter_ads(
    session: requests.Session,
    *,
    max_ads: int,
    page_pause: float,
    request_timeout: int,
    start_page: int = FIRST_PAGE,
    end_page: int | None = None,
) -> Iterator[tuple[dict[str, Any], int, int]]:
    """Yield ``(ad, page_index, rank)`` starting at page 0 (not 1)."""
    for page, rows in iter_pages(
        session,
        max_ads=max_ads,
        page_pause=page_pause,
        request_timeout=request_timeout,
        start_page=start_page,
        end_page=end_page,
    ):
        for ad, rank in rows:
            yield ad, page, rank


def last_completed_sweep_depth() -> int | None:
    """Pages read by the most recent sweep that actually reached the end."""
    return (
        FetchRun.objects.filter(
            mode=FetchRun.Mode.FULL, reached_end=True, status=FetchRun.Status.SUCCEEDED
        )
        .order_by("-started_at")
        .values_list("pages_fetched", flat=True)
        .first()
    )


def end_of_feed_is_credible(depth_reached: int, expected_depth: int | None) -> bool:
    """Is an apparent end-of-feed deep enough to believe?

    ``depth_reached`` is the absolute page index the feed ended at, not the pages
    read by this run — a sweep resuming from a checkpoint at page 619 reads only
    a few hundred pages while still reaching the true bottom.

    Two empty pages in a row are the *only* evidence the feed has ended, and a
    degraded API serves exactly that. Believing it is expensive and silent: the
    run stamps ``reached_end``, so removal detection concludes that everything
    below the truncation point left the market, and crawl_gaps cannot notice
    because its ceiling is the deepest rank actually observed.

    So an end-of-feed is cross-checked against the last sweep that genuinely
    finished. A first-ever sweep has nothing to compare against and is believed.
    """
    if not expected_depth:
        return True
    return depth_reached >= expected_depth * MIN_END_OF_FEED_DEPTH_RATIO


def _delta_floor_pages(now: datetime) -> int:
    """Minimum pages a delta run should read, from the newest coverage row.

    Replaces the old wall-clock ``gap_minutes // 15`` guess: coverage is a
    recorded fact now, so the floor is "how much feed moved since the last page
    we actually read", not "how long has this process been down".
    """
    newest = (
        PageCoverage.objects.order_by("-fetched_at")
        .values_list("fetched_at", flat=True)
        .first()
    )
    if not newest:
        return 0
    minutes = max(0.0, (now - newest).total_seconds() / 60.0)
    return min(
        DELTA_FLOOR_MAX_PAGES, math.ceil(minutes * CHURN_ADS_PER_MIN / PAGE_SIZE)
    )


@contextmanager
def _checkpoint_on_sigterm():
    """Turn SIGTERM into KeyboardInterrupt for the duration of a run.

    Python's default SIGTERM disposition kills the process outright, so
    ``docker compose stop`` mid-sweep skipped every ``except`` clause and the
    run lost its resume checkpoint. Raising instead lets the existing
    interrupt path flush ``resume_from_page`` and pick up where it died.

    ``signal.signal`` only works on the main thread, and the admin job
    endpoints deliberately run commands in daemon threads — there the
    ValueError is expected and the guard is simply a no-op.
    """
    def _raise(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    try:
        previous = signal.signal(signal.SIGTERM, _raise)
    except ValueError:
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _resume_page(mode: str) -> int | None:
    """Checkpoint left by the *most recent* run of this mode, if any.

    Deliberately the latest run rather than the latest checkpointed one: a
    clean run clears its checkpoint, and that clean run must bury an older
    abort's checkpoint instead of letting it resurrect forever.
    """
    return (
        FetchRun.objects.filter(source=FetchRun.Source.LIVE_FETCH, mode=mode)
        .order_by("-created_at")
        .values_list("resume_from_page", flat=True)
        .first()
    )


def fetch_live(**kwargs) -> FetchRun:
    """Public entry point: :func:`_fetch_live` under a SIGTERM checkpoint guard.

    A thin wrapper purely so the signal handler brackets the whole run without
    adding an indentation level to the page loop below.
    """
    with _checkpoint_on_sigterm():
        return _fetch_live(**kwargs)


def _fetch_live(
    *,
    mode: str = "delta",
    max_ads: int | None = None,
    page_pause: float | None = None,
    request_timeout: int | None = None,
    max_stale_pages: int | None = None,
    start_page: int | None = None,
    end_page: int | None = None,
) -> FetchRun:
    """Stream live Bama ads into Postgres via the shared ingest pipeline.

    Modes:

    ``delta``
        Start at page 0, stop after ``max_stale_pages`` consecutive pages that
        produced neither a new ad nor a price change. ``stop_reason`` is
        ``STALE_PAGES`` (or ``END_OF_FEED`` on a short feed).
    ``full``
        Page 0 until the empty page past the last ad: ``reached_end=True``,
        ``stop_reason=END_OF_FEED`` (or ``MAX_ADS`` if the cap bites first).
    ``backfill``
        Explicit ``start_page``..``end_page`` range for gap repair;
        ``stop_reason=MAX_PAGES``.

    Returns the persisted :class:`FetchRun` with ``run.affected_model_ids``
    populated for downstream score refreshes.
    """
    mode = str(mode or FetchRun.Mode.DELTA)
    if mode not in FetchRun.Mode.values:
        raise ValueError(f"unknown fetch mode {mode!r}")
    if mode == FetchRun.Mode.BACKFILL and start_page is None:
        raise ValueError("backfill mode requires start_page")

    max_ads = int(max_ads if max_ads is not None else settings.BAMA_MAX_ADS)
    page_pause = float(
        page_pause if page_pause is not None else settings.BAMA_PAGE_PAUSE
    )
    request_timeout = int(
        request_timeout
        if request_timeout is not None
        else settings.BAMA_REQUEST_TIMEOUT
    )
    if max_stale_pages is None:
        max_stale_pages = DEFAULT_MAX_STALE_PAGES

    if start_page is None:
        # A prior run aborted mid-sweep: pick up where it died instead of
        # re-reading everything before it.
        #
        # Delta never resumes. Its whole job is the top of the feed, and
        # starting a delta at a checkpoint would skip pages 0..N-1 — the newest
        # ads — which is precisely the class of silent loss this rewrite exists
        # to eliminate. Re-reading a few cheap pages is the correct trade; the
        # deep pages an aborted delta missed are recovered by the full sweep and
        # crawl_gaps, which is what those exist for.
        start_page = FIRST_PAGE
        if mode != FetchRun.Mode.DELTA:
            start_page = _resume_page(mode)
            if start_page is None:
                start_page = FIRST_PAGE
    start_page = max(int(start_page), 0)
    if mode == FetchRun.Mode.BACKFILL and end_page is None:
        end_page = start_page

    floor_pages = (
        _delta_floor_pages(djtz.now()) if mode == FetchRun.Mode.DELTA else 0
    )
    # Read before the run row exists so this run cannot be its own yardstick.
    expected_depth = last_completed_sweep_depth() if mode == FetchRun.Mode.FULL else None

    run = FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH,
        status=FetchRun.Status.RUNNING,
        started_at=djtz.now(),
        max_ads=max_ads,
        page_pause=page_pause,
        mode=mode,
        start_page=start_page,
    )

    reset_cache()
    affected_model_ids: set[int] = set()
    # Finer-grained than the model ids: the cohort outlier pass rescores
    # (model, variant, year) groups, and a full-market rescore per fetch is waste.
    affected_cohorts: set[tuple] = set()

    def record_unknown(phrase: str) -> None:
        obj, created = UnknownTimePhrase.objects.get_or_create(phrase=phrase)
        if not created:
            obj.seen_count = (obj.seen_count or 0) + 1
            obj.save(update_fields=["seen_count", "last_seen_at"])
        obj.last_fetch_run = run
        obj.save(update_fields=["last_fetch_run"])

    interrupted = False
    stop_reason: str | None = None
    stale_pages = 0
    total_yielded = 0
    # Where a dying run should restart: the page being processed, bumped to the
    # next one only once the current page is fully committed.
    resume_page = start_page

    try:
        try:
            session = create_session(settings.BAMA_COOKIE or None)
            warmup(session, request_timeout)
            for page, rows in iter_pages(
                session,
                max_ads=max_ads,
                page_pause=page_pause,
                request_timeout=request_timeout,
                start_page=start_page,
                end_page=end_page,
            ):
                if not rows:
                    # A full sweep claims the feed ended here. Believe it only if
                    # it got deep enough to be plausible; a truncated sweep that
                    # stamps reached_end tells every downstream consumer that the
                    # rest of the market disappeared.
                    credible = mode != FetchRun.Mode.FULL or end_of_feed_is_credible(
                        page, expected_depth
                    )
                    if credible:
                        run.reached_end = True
                        stop_reason = FetchRun.StopReason.END_OF_FEED
                    else:
                        run.error = (
                            f"end-of-feed at page {page}; the last completed sweep "
                            f"reached {expected_depth}. Treating as truncated, not "
                            "as the end of the feed."
                        )
                        stop_reason = FetchRun.StopReason.ERROR
                    break

                resume_page = page
                page_new = 0
                page_changed = 0
                page_content_changed = 0
                ranks: list[int] = []

                # One page, one transaction. A crash between the last ad and the
                # PageCoverage row used to leave a half-written page whose
                # coverage row claimed it was complete — and coverage is what
                # crawl_gaps trusts to decide a rank range needs no revisit, so
                # the hole became permanent and invisible.
                with transaction.atomic():
                    for ad, rank in rows:
                        ranks.append(rank)
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
                        result = ingest_ad(
                            extracted,
                            run=run,
                            observed_at=observed_at,
                            publish_at=publish_at,
                            dealer=ad.get("dealer"),
                            rank=rank,
                        )
                        # Rejected => a hard rule fired; the payload is
                        # quarantined in IngestReject and no Ad row exists.
                        if result.ad is None:
                            run.skipped_count += 1
                            continue

                        run.fetched_count += 1
                        if result.created:
                            run.created_count += 1
                            page_new += 1
                        else:
                            run.updated_count += 1
                        if result.price_changed:
                            run.price_change_count += 1
                            page_changed += 1
                        if result.version_created:
                            page_content_changed += 1

                        cohort = result.cohort
                        if cohort:
                            affected_model_ids.add(cohort[0])
                            affected_cohorts.add(cohort)

                        if run.fetched_count % SAVE_EVERY == 0:
                            run.save()

                    total_yielded += len(rows)
                    run.pages_fetched += 1
                    run.deepest_rank = max(run.deepest_rank or 0, max(ranks))
                    PageCoverage.objects.create(
                        fetch_run=run,
                        page_index=page,
                        rank_lo=min(ranks),
                        rank_hi=max(ranks),
                        ad_count=len(rows),
                        new_count=page_new,
                        changed_count=page_changed,
                        fetched_at=djtz.now(),
                    )
                resume_page = page + 1

                if mode == FetchRun.Mode.DELTA:
                    # Stale = the page carried nothing new at all. Content counts:
                    # a page where sellers rewrote descriptions or added photos is
                    # not stale, and judging on new-ads-and-price-moves alone
                    # stopped the crawl while the feed was still moving.
                    if page_new == 0 and page_changed == 0 and page_content_changed == 0:
                        stale_pages += 1
                        if (
                            stale_pages >= max_stale_pages
                            and run.pages_fetched >= floor_pages
                        ):
                            stop_reason = FetchRun.StopReason.STALE_PAGES
                            break
                    else:
                        stale_pages = 0

        except (KeyboardInterrupt, SystemExit):
            # Partial state is already in Postgres; flush as SUCCEEDED.
            #
            # SystemExit as well as KeyboardInterrupt: `docker compose stop`
            # sends SIGTERM, and the handler installed below turns that into
            # SystemExit, which is a BaseException and so slipped past both this
            # clause and the `except Exception` below. The run was flushed with
            # resume_from_page=None, and the next sweep restarted at page 0 —
            # re-walking ~940 pages every container restart.
            interrupted = True

        run.status = FetchRun.Status.SUCCEEDED
        if interrupted:
            run.error = run.error or "interrupted"
            stop_reason = FetchRun.StopReason.INTERRUPTED
        elif stop_reason is None:
            stop_reason = (
                FetchRun.StopReason.MAX_ADS
                if total_yielded >= max_ads
                else FetchRun.StopReason.MAX_PAGES
            )
        run.stop_reason = stop_reason
        # Only an abort leaves a checkpoint; any clean stop restarts at page 0.
        run.resume_from_page = (
            resume_page if stop_reason == FetchRun.StopReason.INTERRUPTED else None
        )
    except Exception as exc:  # noqa: BLE001
        run.status = FetchRun.Status.FAILED
        run.error = str(exc)[:4000]
        run.stop_reason = FetchRun.StopReason.ERROR
        run.resume_from_page = resume_page
        run.finished_at = djtz.now()
        run.save()
        reset_cache()
        raise
    finally:
        run.finished_at = djtz.now()
        run.save()
        reset_cache()

    run.affected_model_ids = affected_model_ids  # type: ignore[attr-defined]
    run.affected_cohorts = affected_cohorts  # type: ignore[attr-defined]
    return run
