"""Django admin — the "individual records live in admin" half of the Control page.

Provenance tables are read-only: hand-editing a fetch run, a reject or a
coverage row would falsify the audit trail that coverage, removal detection and
pruning all read as ground truth.
"""

from django.contrib import admin

from apps.core.models import (
    Ad, FetchRun, IngestReject, JobRun, ListingEpisode, NotifierSettings, PageCoverage,
)


class ReadOnly(admin.ModelAdmin):
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

    list_display = ("code", "title", "brand", "model", "current_price", "status",
                    "publish_at", "last_seen_at")
    list_filter = ("status", "brand", "category", "transmission")
    search_fields = ("code", "title")
    date_hierarchy = "publish_at"


@admin.register(FetchRun)
class FetchRunAdmin(ReadOnly):
    list_display = ("id", "source", "status", "mode", "started_at", "finished_at",
                    "fetched_count", "created_count", "updated_count")
    list_filter = ("source", "status", "mode", "stop_reason")
    search_fields = ("id",)
    date_hierarchy = "created_at"


@admin.register(IngestReject)
class IngestRejectAdmin(ReadOnly):
    """A spike in one ``rule`` reads as "Bama changed their schema"."""

    list_display = ("id", "code", "rule", "observed_at", "fetch_run")
    list_filter = ("rule",)
    search_fields = ("code", "rule", "detail")
    date_hierarchy = "observed_at"


@admin.register(PageCoverage)
class PageCoverageAdmin(ReadOnly):
    list_display = ("id", "fetch_run", "page_index", "rank_lo", "rank_hi",
                    "ad_count", "new_count", "changed_count", "fetched_at")
    list_filter = ("fetch_run__source",)
    search_fields = ("fetch_run__id",)
    date_hierarchy = "fetched_at"


@admin.register(JobRun)
class JobRunAdmin(ReadOnly):
    list_display = ("id", "name", "status", "triggered_by", "attempt",
                    "started_at", "finished_at", "duration_s")
    list_filter = ("name", "status", "triggered_by")
    search_fields = ("name", "detail", "error")
    date_hierarchy = "started_at"


@admin.register(ListingEpisode)
class ListingEpisodeAdmin(ReadOnly):
    list_display = ("id", "ad", "started_at", "ended_at", "is_open",
                    "first_price", "last_price")
    list_filter = ("ended_at",)
    search_fields = ("ad__code",)
    date_hierarchy = "started_at"


@admin.register(NotifierSettings)
class NotifierSettingsAdmin(admin.ModelAdmin):
    """Singleton (pk=1) — one row, never deleted."""

    list_display = ("id", "enabled", "min_discount_pct", "min_peers",
                    "telegram_chat_id", "updated_at")

    def has_add_permission(self, request):
        return not NotifierSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
