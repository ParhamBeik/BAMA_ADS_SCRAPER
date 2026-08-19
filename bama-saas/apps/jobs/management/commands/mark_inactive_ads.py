"""Mark ads that have dropped out of the Bama feed as REMOVED.

The rule is **coverage-based, not wall-clock**. Any ad still listed is seen by a
pass that walks every rank, so "was not seen across two consecutive complete
coverage windows" is a *proof* of absence, where "has not been seen for 14 days"
was only ever a guess.

Coverage is judged over rolling windows from the union of ``PageCoverage``, not
per fetch run. The previous rule demanded two runs that each set
``reached_end`` — one uninterrupted walk of ~936 pages against a host that
answers 503. Measured over 39 days: 11 of 28 full sweeps completed, so removals
could only be detected on the days one happened to finish. Listing episodes
ended on 17 of 39 days in lumps of up to 6,873, and every survival curve
computed from them was reading the sweep schedule rather than the market.

Windows fix that because coverage accumulates: three interrupted sweeps that
jointly walk the feed prove exactly what one clean sweep proves.

Two windows rather than one, because a single pass can miss an ad legitimately:
deletions pull later ads to lower ranks, behind pages the pass already read
(the same asymmetry ``PageCoverage``/``crawl_gaps`` exist for). Requiring two
consecutive misses costs one window of latency and removes that whole class of
false positive.

Safety property: unless **both** windows are provably complete, this command
marks **nothing** and says so. A stalled crawler must never be read as "the
entire inventory disappeared" — the failure mode a wall-clock rule has.

Idempotent: only flips ACTIVE → REMOVED, stamping ``removed_at`` with the ad's
own ``last_seen_at`` (the best estimate of when it left). Re-seeing a removed ad
via ``ingest_ad`` flips it back to ACTIVE and clears ``removed_at``.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import F
from django.utils import timezone

from apps.core.models import Ad
from apps.jobs.services.coverage import COVERAGE_WINDOW_HOURS, coverage_is_complete

# How many consecutive complete coverage windows an ad must be absent from.
REQUIRED_MISSED_WINDOWS = 2


def sweep_cutoff(window_hours: int = COVERAGE_WINDOW_HOURS):
    """Start of the older of two consecutive complete coverage windows.

    Returns ``(cutoff, n_complete_windows)``; ``cutoff`` is None unless both
    windows are complete. An ad whose ``last_seen_at`` predates the cutoff was
    absent from two full passes over the feed.
    """
    now = timezone.now()
    window = timedelta(hours=window_hours)
    recent_start = now - window
    older_start = now - 2 * window

    if not coverage_is_complete(since=recent_start):
        return None, 0
    if not coverage_is_complete(since=older_start, until=recent_start):
        return None, 1
    return older_start, REQUIRED_MISSED_WINDOWS


class Command(BaseCommand):
    help = "Mark ads absent from two consecutive complete coverage windows as REMOVED."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help=(
                "Escape hatch: use the legacy wall-clock rule with this many "
                "days instead of coverage."
            ),
        )
        parser.add_argument(
            "--window-hours",
            type=int,
            default=COVERAGE_WINDOW_HOURS,
            help="Length of one coverage window (default: %(default)s).",
        )

    def handle(self, *args, **options):
        if options["days"] is not None:
            cutoff = timezone.now() - timedelta(days=options["days"])
            basis = f"wall-clock override, last_seen < {cutoff:%Y-%m-%d %H:%M} UTC"
        else:
            window = options["window_hours"]
            cutoff, n_windows = sweep_cutoff(window)
            if cutoff is None:
                self.stdout.write(self.style.WARNING(
                    f"Only {n_windows} of {REQUIRED_MISSED_WINDOWS} consecutive "
                    f"{window}h windows are fully covered; cannot prove an ad is "
                    f"gone. Marked 0 ads REMOVED. Run `crawl_gaps` to close the "
                    f"uncovered rank ranges."
                ))
                return
            basis = (
                f"absent from {REQUIRED_MISSED_WINDOWS} consecutive {window}h "
                f"coverage windows (last_seen < {cutoff:%Y-%m-%d %H:%M} UTC)"
            )

        # Per-row removed_at = last_seen_at via F-expression (single UPDATE).
        count = Ad.objects.filter(
            status=Ad.Status.ACTIVE, last_seen_at__lt=cutoff
        ).update(status=Ad.Status.REMOVED, removed_at=F("last_seen_at"))

        self.stdout.write(self.style.SUCCESS(
            f"Marked {count} ad(s) REMOVED — {basis}."
        ))
