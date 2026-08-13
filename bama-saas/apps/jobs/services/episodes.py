"""Listing episodes: one continuous period during which a code was on the feed.

``Ad`` is overwritten on every observation, so it remembers only the current
state. An episode is the permanent record, and it is the unit survival analysis
actually needs — Kaplan-Meier in ``apps/core/services/liquidity.py`` reads
nothing else.

This module used to also derive *physical car identity* from the per-vehicle
uuid in Bama's image paths. That is gone: 58,879 of 59,033 identities were
singletons, so the dedup found essentially nothing while implying a level of
insight it never delivered.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.core.models import Ad, ListingEpisode


@dataclass
class SyncReport:
    opened: int = 0
    closed: int = 0
    reopened: int = 0

    def as_dict(self) -> dict:
        return {
            "opened": self.opened, "closed": self.closed,
            "reopened": self.reopened,
        }


@transaction.atomic
def sync_episodes(*, limit: int | None = None) -> SyncReport:
    """Bring episodes in line with the current state of every ad.

    Idempotent and derived entirely from ``Ad``, so it can be re-run at any time
    and back-fills history on first run. Deliberately a separate pass rather than
    a hook inside ingestion: an episode ends when an ad stops being seen, which is
    a conclusion no single observation can reach.
    """
    report = SyncReport()
    now = timezone.now()

    open_by_ad = {
        e.ad_id: e for e in ListingEpisode.objects.filter(ended_at__isnull=True)
    }

    ads = Ad.objects.all().only(
        "code", "status", "first_seen_at", "last_seen_at", "removed_at",
        "current_price", "model_id", "year_jalali",
    )
    if limit:
        ads = ads[:limit]

    to_create, to_update = [], []
    for ad in ads.iterator(chunk_size=1000):
        episode = open_by_ad.get(ad.code)

        if ad.status == Ad.Status.ACTIVE:
            if episode is None:
                # Either the first sighting, or the ad came back after removal —
                # a reappearance opens a NEW episode rather than resurrecting the
                # old one, because the gap in between is the interesting part.
                previous = ListingEpisode.objects.filter(ad_id=ad.code).exists()
                to_create.append(ListingEpisode(
                    ad=ad,
                    started_at=ad.first_seen_at if not previous else (ad.last_seen_at or now),
                    first_price=ad.current_price,
                    last_price=ad.current_price,
                ))
                report.reopened += 1 if previous else 0
                report.opened += 0 if previous else 1
            elif episode.last_price != ad.current_price:
                episode.last_price = ad.current_price
                to_update.append(episode)
        elif episode is not None:
            episode.ended_at = ad.removed_at or ad.last_seen_at or now
            episode.last_price = ad.current_price
            to_update.append(episode)
            report.closed += 1
        elif not ListingEpisode.objects.filter(ad_id=ad.code).exists():
            # Already removed before episodes existed. Its whole life is still
            # derivable from first_seen_at/removed_at, and without this the first
            # backfill would silently discard every ad that had already left —
            # which is most of the history, and precisely the population that
            # survival analysis is about.
            to_create.append(ListingEpisode(
                ad=ad,
                started_at=ad.first_seen_at,
                ended_at=ad.removed_at or ad.last_seen_at or now,
                first_price=ad.current_price,
                last_price=ad.current_price,
            ))
            report.closed += 1

    if to_create:
        ListingEpisode.objects.bulk_create(to_create, batch_size=500)
    if to_update:
        ListingEpisode.objects.bulk_update(
            to_update, ["ended_at", "last_price"], batch_size=500
        )

    return report
