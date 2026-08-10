"""Listing episodes and physical-car identity.

Two questions this answers that ``Ad`` alone cannot:

*How long was this listed?* ``Ad`` is overwritten on every observation, so it
remembers only the current state. An episode records one continuous period on
the feed, which is the unit survival analysis actually needs.

*Is this the same car?* Bama stores an ad's photos under a per-vehicle path,
``/VehicleCarImages/<uuid>/CarImage_....jpg``, and reuses that uuid when the same
car is listed again. Matching on it is near-conclusive; matching on model, year,
mileage and price is not, because a thousand identical Prides share all four.

Two episodes sharing an identity mean different things depending on their dates:

* **overlapping** — the same car is on the feed twice right now. Inventory counts
  it twice, so supply is overstated.
* **sequential** — the car was relisted. Without linking them, the tenure clock
  restarts and one delisting is counted that never happened, biasing every
  survival curve toward "sells fast".

Both are derived from the dates rather than stored, so there is no separate link
table to keep consistent with the episodes it describes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.core.models import Ad, ListingEpisode, VehicleIdentity

# The per-vehicle folder in Bama's image CDN path. Everything after it (file
# name, size suffix, ``?x-img=`` transform parameters) varies between renditions
# of the same photo and must not be part of the key.
_VEHICLE_UUID = re.compile(r"VehicleCarImages/([0-9a-fA-F-]{36})/")

# Bumped when the matching rule changes. Stored on every identity so a link can
# be traced to the logic that produced it.
ALGORITHM_VERSION = 1


def image_identity_key(payload: dict) -> str | None:
    """The vehicle key for one payload, or None when there is no usable evidence.

    Returns None rather than guessing: an ad with no photos has no evidence, and
    an identity built on absence would merge every photoless listing into one car.
    """
    images = (payload or {}).get("images") or []
    for image in images:
        if not isinstance(image, dict):
            continue
        for url in image.values():
            match = _VEHICLE_UUID.search(url) if isinstance(url, str) else None
            if match:
                return match.group(1).lower()
    return None


def _identity_for(ad: Ad) -> VehicleIdentity | None:
    key = image_identity_key(ad.raw_payload or {})
    if not key:
        return None
    identity, _ = VehicleIdentity.objects.get_or_create(
        key=key,
        defaults={
            "method": VehicleIdentity.Method.IMAGE_ASSET,
            "algorithm_version": ALGORITHM_VERSION,
            "evidence": {"source": "images[].url", "uuid": key},
        },
    )
    return identity


def _identity_is_consistent(identity: VehicleIdentity, ad: Ad) -> bool:
    """Guard against a reused image folder describing a different car.

    The uuid is strong evidence but not a promise. If the episodes already
    attached to it disagree with this ad on model or model year, the safe reading
    is that the evidence is bad — leave the ad unlinked rather than merge two
    cars' histories, which is not recoverable once other tables are built on it.
    """
    others = (
        ListingEpisode.objects.filter(identity=identity)
        .exclude(ad_id=ad.code)
        .select_related("ad")[:5]
    )
    for episode in others:
        if episode.ad.model_id and ad.model_id and episode.ad.model_id != ad.model_id:
            return False
        if (
            episode.ad.year_jalali and ad.year_jalali
            and episode.ad.year_jalali != ad.year_jalali
        ):
            return False
    return True


@dataclass
class SyncReport:
    opened: int = 0
    closed: int = 0
    reopened: int = 0
    identified: int = 0

    def as_dict(self) -> dict:
        return {
            "opened": self.opened, "closed": self.closed,
            "reopened": self.reopened, "identified": self.identified,
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
        "current_price", "model_id", "year_jalali", "raw_payload",
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

    report.identified = attach_identities()
    return report


def attach_identities(*, limit: int | None = None) -> int:
    """Attach a vehicle identity to episodes that do not have one yet."""
    episodes = (
        ListingEpisode.objects.filter(identity__isnull=True)
        .select_related("ad")
    )
    if limit:
        episodes = episodes[:limit]

    attached = 0
    for episode in episodes.iterator(chunk_size=500):
        identity = _identity_for(episode.ad)
        if identity is None or not _identity_is_consistent(identity, episode.ad):
            continue
        episode.identity = identity
        episode.save(update_fields=["identity"])
        attached += 1
    return attached


def episodes_for_identity(identity_id: int) -> list[ListingEpisode]:
    return list(
        ListingEpisode.objects.filter(identity_id=identity_id)
        .select_related("ad")
        .order_by("started_at")
    )


def classify_pair(earlier: ListingEpisode, later: ListingEpisode) -> str:
    """``duplicate`` when the two overlap in time, ``relist`` when sequential.

    The distinction is the whole point. Overlapping means the same car is counted
    twice in today's inventory; sequential means one apparent delisting never
    happened and the second listing's clock should not restart.
    """
    earlier_end = earlier.ended_at
    if earlier_end is None or later.started_at < earlier_end:
        return "duplicate"
    return "relist"


def identity_summary(identity_id: int) -> dict:
    """Everything known about one physical car, for the ad-identity endpoint."""
    episodes = episodes_for_identity(identity_id)
    pairs = [
        {
            "earlier": a.ad_id, "later": b.ad_id,
            "relation": classify_pair(a, b),
        }
        for a, b in zip(episodes, episodes[1:])
    ]
    return {
        "identity_id": identity_id,
        "listing_count": len(episodes),
        "episodes": [
            {
                "code": e.ad_id,
                "started_at": e.started_at,
                "ended_at": e.ended_at,
                "first_price": e.first_price,
                "last_price": e.last_price,
            }
            for e in episodes
        ],
        "relations": pairs,
        "relisted": any(p["relation"] == "relist" for p in pairs),
        "duplicated": any(p["relation"] == "duplicate" for p in pairs),
    }
