"""Django admin for debugging the crawl — the Control page's "individual records
live in Django admin" promise. Only the models worth eyeballing by hand are
registered here; provenance tables are read-only because hand-editing a fetch
run, a reject, or a coverage row would falsify the audit trail those services
(coverage gap-scan, mark_inactive_ads, prune) read as ground truth.
"""

from django.contrib import admin

from apps.core.models import (
    Ad,
    FetchRun,
    IngestReject,
    JobRun,
    ListingEpisode,
    NotifierSettings,
    PageCoverage,
)


class ReadOnlyProvenanceAdmin(admin.ModelAdmin):
    """Base for append-only audit tables: viewable, never add/change/delete."""

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Ad)
class AdAdmin(admin.ModelAdmin):
    """The mutable current snapshot — editable for one-off corrections."""

    list_display = ("code", "title", "brand", "model", "current_price", "status", "publish_at", "last_seen_at")
    list_filter = ("status", "brand", "category", "transmission")
    search_fields = ("code", "title")
    date_hierarchy = "publish_at"


@admin.register(FetchRun)
class FetchRunAdmin(ReadOnlyProvenanceAdmin):
    list_display = ("id", "source", "status", "mode", "started_at", "finished_at", "fetched_count", "created_count", "updated_count")
    list_filter = ("source", "status", "mode", "stop_reason")
    search_fields = ("id",)
    date_hierarchy = "created_at"


@admin.register(IngestReject)
class IngestRejectAdmin(ReadOnlyProvenanceAdmin):
    """Quarantine table — a spike in one ``rule`` reads as "Bama changed schema"."""

    list_display = ("id", "code", "rule", "observed_at", "fetch_run")
    list_filter = ("rule", "code")
    search_fields = ("code", "rule", "detail")
    date_hierarchy = "observed_at"


@admin.register(PageCoverage)
class PageCoverageAdmin(ReadOnlyProvenanceAdmin):
    """The crawl's ledger — coverage.find_gaps and mark_inactive_ads read this."""

    list_display = ("id", "fetch_run", "page_index", "rank_lo", "rank_hi", "ad_count", "new_count", "changed_count", "fetched_at")
    list_filter = ("fetch_run__source",)
    search_fields = ("fetch_run__id",)
    date_hierarchy = "fetched_at"


@admin.register(JobRun)
class JobRunAdmin(ReadOnlyProvenanceAdmin):
    """Per-scheduled-step outcome log — the only trace of non-fetch worker steps."""

    list_display = ("id", "name", "status", "triggered_by", "attempt", "started_at", "finished_at", "duration_s")
    list_filter = ("name", "status", "triggered_by")
    search_fields = ("name", "detail", "error")
    date_hierarchy = "started_at"


@admin.register(ListingEpisode)
class ListingEpisodeAdmin(ReadOnlyProvenanceAdmin):
    """Permanent lifecycle record — hand-editing would corrupt the tenure clock."""

    list_display = ("id", "ad", "started_at", "ended_at", "is_open", "first_price", "last_price")
    list_filter = ("ended_at",)
    search_fields = ("ad__code",)
    date_hierarchy = "started_at"


@admin.register(NotifierSettings)
class NotifierSettingsAdmin(admin.ModelAdmin):
    """Singleton (pk=1, via ``NotifierSettings.load()``) — one row, never deleted."""

    list_display = ("id", "enabled", "min_discount_pct", "min_peers", "telegram_chat_id", "updated_at")

    def has_add_permission(self, request):
        return not NotifierSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
