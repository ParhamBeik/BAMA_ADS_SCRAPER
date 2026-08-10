"""Recompute normalized columns on existing Ad rows from their raw payloads.

Needed once after the normalization migration, because two columns were written
wrong for every row ingested before it:

- ``year_jalali`` / ``year_gregorian`` / ``year_calendar`` did not exist. ``year``
  holds whichever calendar Bama happened to use for that brand, so cohorts keyed
  on it silently split the same model year into two groups.
- ``mileage`` is NULL for every zero-kilometer car. ``detail.mileage`` is the
  string "صفر کیلومتر" for those, and ``parse_int(positive=True)`` mapped it to
  None. That is ~33% of ads in the seed data, all of them brand-new cars, all
  silently excluded from mileage filters and statistics.
- ``quality_flags`` is ``[]`` on every legacy row because verification did not
  exist when they were written — and ``[]`` is exactly what ``verified()`` reads
  as "clean", so unverified rows would bypass every rule and reach analytics
  untested. Re-verifying from the stored payload closes that.

What this CANNOT repair: ``AdObservation.rank`` was never recorded, and feed
position is a point-in-time fact that cannot be reconstructed after the fact —
only new fetches carry it. Likewise, any ad that appeared and disappeared while
still inside the skipped top-30 rank window was never fetched at all and is
simply absent; there is nothing on disk to repair.

Rows that come out of re-verification with a *hard* flag are unrepairable by
definition — Bama itself sent the bad value, so no amount of re-parsing fixes
them — and are therefore DELETED rather than left in place behind ``verified()``.
This mirrors ``ingest_ad``, which no longer persists hard-rejected ads at all, so
the two paths agree and a purged row is not re-created by the next fetch.
Deletion cascades to the row's versions/observations/prices; the payload itself
survives in ``IngestReject`` for any row that a live fetch rejected.

``raw_payload`` is the source of truth here — ``Ad.mileage`` cannot be repaired
from itself, since the information was destroyed on the way in.

Idempotent and resumable: rows are walked in ``code`` order, and ``--since-code``
restarts from where a previous run stopped. Re-running over already-fixed rows
changes nothing.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.models import Ad
from apps.jobs.services.ingest import _presentation_fields
from apps.jobs.services.verify import HARD_RULE_IDS, verify_extracted
from apps.parsing import extract_ad, normalize_model_year, parse_mileage

FIELDS = (
    "year_jalali",
    "year_gregorian",
    "year_calendar",
    "mileage",
    "canonical_path",
    "quality_flags",
    "image_count",
    "description_length",
    "seller_authenticated",
    "source_modified_at",
)


class Command(BaseCommand):
    help = "Backfill year_jalali/year_gregorian/year_calendar, mileage and canonical_path from raw_payload."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=2000)
        parser.add_argument(
            "--since-code",
            default=None,
            help="Resume from this ad code (exclusive). Codes are walked in ascending order.",
        )
        parser.add_argument("--limit", type=int, default=None, help="Stop after N rows.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing.",
        )

    def handle(self, *args, **opts):
        batch_size = opts["batch_size"]
        qs = Ad.objects.order_by("code")
        if opts["since_code"]:
            qs = qs.filter(code__gt=opts["since_code"])
        if opts["limit"]:
            qs = qs[: opts["limit"]]

        scanned = changed = purged = 0
        year_fixed = mileage_fixed = path_fixed = flags_fixed = 0
        presentation_fixed = 0
        last_code = None
        pending: list[Ad] = []
        doomed: list[str] = []

        for ad in qs.iterator(chunk_size=batch_size):
            scanned += 1
            last_code = ad.code
            detail = (ad.raw_payload or {}).get("detail") or {}

            # Prefer the raw payload; fall back to the stored column so rows whose
            # payload is missing still get their calendar classified.
            yj, yg, calendar = normalize_model_year(detail.get("year", ad.year))
            mileage = parse_mileage(detail.get("mileage"))
            canonical_path = (detail.get("url") or "").strip()

            dirty = False
            if (ad.year_jalali, ad.year_gregorian, ad.year_calendar) != (yj, yg, calendar):
                ad.year_jalali, ad.year_gregorian, ad.year_calendar = yj, yg, calendar
                year_fixed += 1
                dirty = True
            # Only overwrite mileage when the payload actually yields a value;
            # never clobber a good number with None because a payload went missing.
            if mileage is not None and ad.mileage != mileage:
                ad.mileage = mileage
                mileage_fixed += 1
                dirty = True
            if canonical_path and ad.canonical_path != canonical_path:
                ad.canonical_path = canonical_path[:400]
                path_fixed += 1
                dirty = True

            # Listing-presentation fields promoted out of raw_payload. Backfilled
            # here rather than in a command of their own: this loop already walks
            # every ad and already has the payload's detail dict open.
            for attr, value in _presentation_fields(detail).items():
                if value is not None and getattr(ad, attr) != value:
                    setattr(ad, attr, value)
                    presentation_fixed += 1
                    dirty = True

            # Rows ingested before verification existed default to [], which
            # verified() reads as "clean" — so legacy data would bypass every
            # rule. Re-extract from the stored payload and verify it now.
            # No IngestReject rows are written here: there is no FetchRun to
            # attribute them to, and the payload is already on the Ad row.
            if ad.raw_payload:
                extracted = extract_ad(ad.raw_payload, ad.last_seen_at or ad.first_seen_at)
                if extracted is not None:
                    flags = [r.rule for r in verify_extracted(extracted, ad.raw_payload)]
                    # A hard flag means the source value is bad, not our parse of
                    # it, so there is nothing to repair — drop the row instead of
                    # keeping unusable data hidden behind verified().
                    if HARD_RULE_IDS.intersection(flags):
                        doomed.append(ad.code)
                        purged += 1
                        if len(doomed) >= batch_size:
                            self._purge(doomed, opts["dry_run"])
                            doomed = []
                        continue
                    if ad.quality_flags != flags:
                        ad.quality_flags = flags
                        flags_fixed += 1
                        dirty = True

            if dirty:
                changed += 1
                pending.append(ad)

            if len(pending) >= batch_size:
                self._flush(pending, opts["dry_run"])
                pending = []
                self.stdout.write(f"  … {scanned} scanned, {changed} updated (at {last_code})")

        self._flush(pending, opts["dry_run"])
        self._purge(doomed, opts["dry_run"])

        verb = "would update" if opts["dry_run"] else "updated"
        purge_verb = "would purge" if opts["dry_run"] else "purged"
        self.stdout.write(self.style.SUCCESS(
            f"Backfill done. scanned={scanned} {verb}={changed} "
            f"(year={year_fixed} mileage={mileage_fixed} canonical_path={path_fixed} "
            f"quality_flags={flags_fixed} presentation={presentation_fixed}) "
            f"{purge_verb}={purged} last_code={last_code}"
        ))

    @staticmethod
    def _flush(rows: list[Ad], dry_run: bool) -> None:
        if not rows or dry_run:
            return
        with transaction.atomic():
            Ad.objects.bulk_update(rows, FIELDS)

    @staticmethod
    def _purge(codes: list[str], dry_run: bool) -> None:
        """Delete unrepairable rows. Cascades to versions/observations/prices."""
        if not codes or dry_run:
            return
        with transaction.atomic():
            Ad.objects.filter(code__in=codes).delete()
