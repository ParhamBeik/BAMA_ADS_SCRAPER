# Storage policy (decided 2026-10-02)

Bama runs on one 100 GB VPS shared with one other app (portfolio), with **no backups**.
Every change that stores data must follow these rules. Read before adding a
column, a volume, a downloader or a cache.

## Rules

1. **One local photo per ad: the front photo.** `AdVersionPhoto` holds only
   `position=0` of the ad's current version; `photo_archive` fetches only that.
   The `/api/img/<code>/thumb/` endpoint serves the local copy first.
   Only ads still for sale are archived. A removed ad keeps its photo for
   **30 days** after `removed_at`, then `prune` deletes the row and the file
   (decided 2026-10-13: ~1,000 removals a day would otherwise add ~21 MB a day
   with no end, and no screen or metric reads a removed ad's photo).
2. **Every other photo is a link.** The gallery lives in `Ad.image_urls` and is
   served through the image proxy (Redis cache, then redirect). Never archive
   gallery photos or photos of old versions.
3. **Record changes, not sightings.** An `AdObservation` is written only when
   an ad is new, its content changed (new `AdVersion`) or its price changed
   (new `PriceObservation`). An unchanged re-sighting only updates
   `Ad.last_seen_at`. `prune` still drops observations older than
   `PRUNE_DEFAULT_DAYS` (7); the durable change history is `AdVersion` +
   `PriceObservation`, and lifecycle analytics read `ListingEpisode`.
4. **Keep version history.** `AdVersion.payload` is the only record of how an ad
   changed; do not drop it.
5. **No backups or second copies** on the VPS, the Mac or external drives,
   until a backup plan is written down and approved.

## Why

Archiving every gallery photo of every version needed ~13 GB and grew without
limit; a link plus one front photo gives the same UI for ~3 GB. The disk monitor
pauses collectors at 95% use.

## Cleanup

`prune` (maintenance cadence, ~6 h) enforces rule 1 continuously: old-version
photo rows, photos of ads removed over 30 days ago, then orphan files.
`manage.py compact_photos --dry-run`, then without `--dry-run`, also removes
gallery rows and creates missing front-photo rows.
