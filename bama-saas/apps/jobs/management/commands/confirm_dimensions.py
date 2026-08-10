"""Review and confirm catalog dimensions that ingestion invented.

Brand and model are parsed out of free-text ad titles, so ingestion creates them
unconfirmed and flags the ads that did it (``unknown_dimension``). This command is
the other half of that loop: without a way to clear the backlog the flag would
only ever accumulate.

    manage.py confirm_dimensions                  # list what is pending
    manage.py confirm_dimensions --brand toyota   # confirm one brand
    manage.py confirm_dimensions --all            # confirm everything pending

``--all`` is the cold-start path: on a fresh database every dimension is new by
definition, so the first import legitimately flags the entire catalog.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Count

from apps.core.models import Brand, Model


class Command(BaseCommand):
    help = "List or confirm catalog dimensions created by ingestion."

    def add_arguments(self, parser):
        parser.add_argument("--brand", help="Confirm one brand by slug (and its models).")
        parser.add_argument("--all", action="store_true", help="Confirm every pending row.")
        parser.add_argument(
            "--aliases", action="store_true",
            help="Report model rows that look like two names for one car.",
        )

    def _report_aliases(self):
        """Model rows that are probably the same car under two names.

        Bama edits model names in ad titles — "تیگو 8 پرو مکس (F8 PRO MAX)" became
        "تیگو 8 پرو مکس (F8)" — and each spelling becomes its own catalog row, so
        one car's cohort is split in two and every per-cohort statistic is computed
        on half its population.

        The evidence used is deliberately narrow: the same *ad code* observed under
        two different model names. One physical listing cannot be two models, so
        this is near-conclusive. Name similarity is not used — "سوناتا" and
        "سوناتا هیبرید" are genuinely different cars, and so are "جنسیس" and
        "جنسیس کوپه", so a string-distance heuristic reports mostly false pairs.
        """
        from collections import defaultdict

        from apps.core.models import AdVersion

        names_by_code = defaultdict(set)
        rows = AdVersion.objects.values_list("ad_id", "payload__detail__title")
        for code, title in rows.iterator(chunk_size=5000):
            if title and "،" in title:
                names_by_code[code].add(title.split("،", 1)[1].strip())

        pairs: dict[tuple, int] = defaultdict(int)
        for names in names_by_code.values():
            if len(names) > 1:
                pairs[tuple(sorted(names))] += 1

        if not pairs:
            self.stdout.write("no aliasing detected")
            return
        self.stdout.write("Model names seen on one and the same ad code:")
        for names, n in sorted(pairs.items(), key=lambda kv: -kv[1]):
            self.stdout.write(f"  {n:>5} ad(s): {' == '.join(names)}")

    def handle(self, *args, **options):
        if options["aliases"]:
            self._report_aliases()
            return

        brands = Brand.objects.filter(is_confirmed=False)
        models = Model.objects.filter(is_confirmed=False)

        if options["all"]:
            n_b, n_m = brands.update(is_confirmed=True), models.update(is_confirmed=True)
            self.stdout.write(self.style.SUCCESS(f"confirmed {n_b} brand(s), {n_m} model(s)"))
            return

        if options["brand"]:
            slug = options["brand"]
            if not Brand.objects.filter(slug=slug).exists():
                self.stderr.write(self.style.ERROR(f"no brand with slug {slug!r}"))
                return
            n_b = Brand.objects.filter(slug=slug).update(is_confirmed=True)
            n_m = Model.objects.filter(brand__slug=slug, is_confirmed=False).update(
                is_confirmed=True
            )
            self.stdout.write(self.style.SUCCESS(f"confirmed {n_b} brand(s), {n_m} model(s)"))
            return

        # Ad counts turn the list into a triage order: a bogus dimension from a
        # one-off parse failure carries a handful of ads, a real new model arrives
        # with many.
        pending_brands = brands.annotate(n=Count("ads")).order_by("-n")
        pending_models = models.annotate(n=Count("ads")).order_by("-n")

        if not pending_brands and not pending_models:
            self.stdout.write("nothing pending — the catalog is fully confirmed")
            return

        for brand in pending_brands:
            self.stdout.write(f"BRAND  {brand.slug:<40} {brand.name_fa:<30} {brand.n} ads")
        for model in pending_models:
            self.stdout.write(
                f"MODEL  {model.brand.name_fa:<20} {model.name_fa:<30} {model.n} ads"
            )
        self.stdout.write(
            f"\n{len(pending_brands)} brand(s), {len(pending_models)} model(s) pending. "
            f"Confirm with --brand <slug> or --all."
        )
