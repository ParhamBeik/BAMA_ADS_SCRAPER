# Data quality and model explorer rollout

The new schema keeps all existing `AdVersion`, `AdObservation`, and
`PriceObservation` rows. `ARCHIVE_ADMISSION_REQUIRED` defaults to `false`; the
production catalog must stay on that setting until the archive and identity
backfill have been reviewed. PostgreSQL remains the source of truth.

1. Take and verify a PostgreSQL backup. Mount `/archive` on persistent VPS
   storage. Set the archive cap to 5 GiB and reserve 8 GiB free. The photo
   backup destination is `/Users/parham/Backups/BamaPhotos` on the owner's Mac.
   Install `deploy/backup_photos_to_mac.sh` at
   `/Users/parham/.local/bin/bama-photo-backup` and the supplied LaunchAgent at
   `~/Library/LaunchAgents/com.bama.photo-backup.plist`. It pulls every six
   hours while the Mac is awake, verifies all hashes, restores one sample,
   then acknowledges verified copies to PostgreSQL. Set
   `PHOTO_BACKUP_MODE=mac_pull` on the VPS after one successful run. Catalog
   admission waits for the Mac copy acknowledgment as well as a cash price,
   archived image, and reviewed identity.
2. Run `python manage.py migrate`, then `python manage.py audit_data
   --cleanup-preview` and save its JSON. The preview only simulates exclusion
   from the verified catalog; it never deletes observations or ads.
3. Run `python manage.py backfill_evidence --pilot --limit 100000` and review
   the 206/207 counts. Create reviewed `SourceModelAlias` records in staff admin
   for ambiguous families. Only then run the same command with `--apply`.
   The pilot selects ads whose current identity is 206/207. Review historical
   versions whose ad code now names a different car as identity conflicts.
   Repeat with `--known-mixed` for known merged families, then without either
   selector for the remaining catalog.
4. Run the `photo_archive` job and the Mac pull job. Confirm the Mac job's
   restore verification, check `/api/admin/data-quality/`, and compare a second
   `audit_data --cleanup-preview` JSON with the first. Rebuild snapshots and
   deal scores after identity changes; retrain pricing on the repost-deduplicated
   time holdout before promoting a learned model.
5. Enable `ARCHIVE_ADMISSION_REQUIRED=true` only after the staff report shows
   the expected verified population and backup health. A new cash-priced ad
   waits for an archived image and reviewed identity. Failed photos, ambiguous
   identity, and legacy photo evidence stay outside the verified catalog.

Production deployment, data deletion, and enabling the admission flag require
separate authorization. The application does not infer a sale from a blocked
detail request or an incomplete feed sweep.
