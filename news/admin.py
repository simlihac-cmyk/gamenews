from django.contrib import admin
from django.db.models import Count

from .models import (
    Franchise,
    Issue,
    NewsItem,
    NewsItemFranchise,
    NewsItemIssue,
    Notification,
    RawItem,
    Source,
)


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "source_type",
        "trust_type",
        "region",
        "language",
        "enabled",
        "last_checked_at",
        "last_success_at",
        "last_new_items_count",
        "item_count",
    )
    list_filter = ("source_type", "trust_type", "region", "language", "enabled")
    search_fields = ("name", "slug", "url", "last_error")
    readonly_fields = ("created_at", "updated_at", "last_checked_at", "last_success_at", "last_new_items_count")
    prepopulated_fields = {"slug": ("name",)}

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(item_total=Count("news_items"))

    @admin.display(description="items")
    def item_count(self, obj: Source) -> int:
        return obj.item_total


@admin.register(RawItem)
class RawItemAdmin(admin.ModelAdmin):
    list_display = ("title", "source", "published_at", "first_seen_at", "canonical_url")
    list_filter = ("source", "published_at", "first_seen_at")
    search_fields = ("title", "url", "canonical_url", "source__name", "raw_text")
    readonly_fields = ("first_seen_at", "collected_at", "content_hash")
    date_hierarchy = "published_at"


class NewsItemFranchiseInline(admin.TabularInline):
    model = NewsItemFranchise
    extra = 0


class NewsItemIssueInline(admin.TabularInline):
    model = NewsItemIssue
    extra = 0


@admin.register(NewsItem)
class NewsItemAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "source",
        "trust_label",
        "category",
        "importance_score",
        "is_read",
        "is_bookmarked",
        "published_at",
    )
    list_filter = (
        "trust_label",
        "category",
        "source",
        "region",
        "language",
        "is_read",
        "is_bookmarked",
        "is_archived",
        "importance_score",
    )
    search_fields = ("title", "url", "canonical_url", "source__name", "summary_ko")
    readonly_fields = ("created_at", "updated_at", "normalized_title")
    date_hierarchy = "published_at"
    inlines = [NewsItemFranchiseInline, NewsItemIssueInline]


@admin.register(Issue)
class IssueAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "confidence_score", "first_seen_at", "last_updated_at", "official_confirmed_at")
    list_filter = ("status",)
    search_fields = ("title", "canonical_topic")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "last_updated_at"


@admin.register(Franchise)
class FranchiseAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "priority")
    search_fields = ("name", "slug", "aliases")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("news_item", "channel", "status", "sent_at")
    list_filter = ("channel", "status")
    search_fields = ("news_item__title", "error")
    readonly_fields = ("sent_at",)
