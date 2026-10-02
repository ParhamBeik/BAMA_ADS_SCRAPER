# Storage policy (decided 2026-10-02)

Bama runs on one 100 GB VPS shared with three other apps, with **no backups**.
Every change that stores data must follow these rules. Read before adding a
column, a volume, a downloader or a cache.

## Rules

1. **One local photo per ad: the front photo.** `AdVersionPhoto` holds only
   `position=0` of the ad's current version; `photo_archive` fetches only that.
   The `/api/img/<code>/thumb/` endpoint serves the local copy first.
2. **Every other photo is a link.** The gallery lives in `Ad.image_urls` and is
   served through the image proxy (Redis cache, then redirect). Never archive
   gallery photos or photos of old versions.
3. **Observations are short-lived provenance.** `prune` deletes
   `AdObservation` rows older than `PRUNE_DEFAULT_DAYS` (7). Lifecycle analytics
   read `ListingEpisode`, not observations.
4. **Keep version history.** `AdVersion.payload` is the only record of how an ad
   changed; do not drop it.
5. **No backups or second copies** on the VPS, the Mac or external drives,
   until a backup plan is written down and approved.

## Why

Archiving every gallery photo of every version needed ~13 GB and grew without
limit; a link plus one front photo gives the same UI for ~3 GB. The disk monitor
pauses collectors at 95% use.

## Cleanup

`manage.py compact_photos --dry-run`, then without `--dry-run`, removes rows and
files that break rules 1–2 and creates missing front-photo rows.
