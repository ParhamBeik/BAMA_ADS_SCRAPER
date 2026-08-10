"""Mark ads that have dropped out of the Bama feed as REMOVED.

The rule is **coverage-based, not wall-clock**. A full sweep walks page 0 to the
end of the feed, so any ad still listed is necessarily seen by it. That makes
"was not seen by the last two completed sweeps" a *proof* of absence, where
"has not been seen for 14 days" was only ever a guess — and a badly calibrated
one: with a 6-hourly sweep it left delisted ads sitting ACTIVE for two weeks,
inflating live inventory, holding ``MarketSnapshot.removed_count`` at zero, and
putting a systematic +14 d floor under every time-on-feed statistic.

Two sweeps rather than one, because a single sweep can miss an ad legitimately:
deletions pull later ads to lower ranks, behind pages the sweep already read
(the same asymmetry ``PageCoverage``/``crawl_gaps`` exist for). Requiring two
consecutive misses costs one sweep interval of latency and removes that whole
class of false positive.

Safety property: if fewer than two completed sweeps exist, this command marks
**nothing** and says so. A broken or never-run sweep must never be read as "the
entire inventory disappeared" — that failure mode would wipe the live market in
one tick, which is exactly what a wall-clock rule does when the crawler stalls.

Idempotent: only flips ACTIVE → REMOVED, stamping ``removed_at`` with the ad's
own ``last_seen_at`` (the best estimate of when it left). Re-seeing a removed ad
via ``ingest_ad`` flips it back to ACTIVE and clears ``removed_at``.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import F
from django.utils import timezone

from apps.core.models import Ad, FetchRun

# How many completed sweeps an ad must be absent from before it counts as gone.
REQUIRED_MISSED_SWEEPS = 2


def sweep_cutoff() -> tuple[object | None, int]:
    """Start time of the Nth-most-recent completed sweep, and how many exist.

    "Completed" is ``reached_end=True``: the sweep saw the empty page past the
    last ad, so its coverage of the feed is total. A sweep that stopped early
    proves nothing about the ads it never reached and must not be counted.

    Returns ``(cutoff, n_sweeps)``; ``cutoff`` is None when there are too few.
    An ad whose ``last_seen_at`` predates the cutoff was absent from every
    completed sweep since, inclusive.
    """
    starts = list(
        FetchRun.objects.filter(
            reached_end=True, status=FetchRun.Status.SUCCEEDED
        )
        .order_by("-started_at")
        .values_list("started_at", flat=True)[:REQUIRED_MISSED_SWEEPS]
    )
    if len(starts) < REQUIRED_MISSED_SWEEPS:
        return None, len(starts)
    return starts[-1], len(starts)


class Command(BaseCommand):
    help = "Mark ads absent from the last two completed sweeps as REMOVED."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help=(
                "Escape hatch: use the legacy wall-clock rule with this many "
                "days instead of sweep coverage."
            ),
        )

    def handle(self, *args, **options):
        if options["days"] is not None:
            cutoff = timezone.now() - timedelta(days=options["days"])
            basis = f"wall-clock override, last_seen < {cutoff:%Y-%m-%d %H:%M} UTC"
        else:
            cutoff, n_sweeps = sweep_cutoff()
            if cutoff is None:
                self.stdout.write(self.style.WARNING(
                    f"Only {n_sweeps} completed sweep(s) on record; "
                    f"{REQUIRED_MISSED_SWEEPS} are required to prove an ad is "
                    f"gone. Marked 0 ads REMOVED. Run "
                    f"`fetch_live --mode full` until it reports reached_end=True."
                ))
                return
            basis = (
                f"absent from the last {REQUIRED_MISSED_SWEEPS} completed sweeps "
                f"(last_seen < {cutoff:%Y-%m-%d %H:%M} UTC)"
            )

        # Per-row removed_at = last_seen_at via F-expression (single UPDATE).
        count = Ad.objects.filter(
            status=Ad.Status.ACTIVE, last_seen_at__lt=cutoff
        ).update(status=Ad.Status.REMOVED, removed_at=F("last_seen_at"))

        self.stdout.write(self.style.SUCCESS(
            f"Marked {count} ad(s) REMOVED — {basis}."
        ))
