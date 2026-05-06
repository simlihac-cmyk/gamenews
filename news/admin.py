from django.contrib import admin, messages
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from .models import (
    Franchise,
    Issue,
    IssueStatus,
    NewsItem,
    NewsItemFranchise,
    NewsItemIssue,
    Notification,
    RawItem,
    Source,
    UserFranchiseFavorite,
)


ISSUE_STATUS_PRECEDENCE = {
    IssueStatus.CONFIRMED: 50,
    IssueStatus.DEBUNKED: 45,
    IssueStatus.DEVELOPING: 30,
    IssueStatus.RUMOR: 20,
    IssueStatus.STALE: 10,
}


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
        "last_fetch_duration_seconds",
        "average_fetch_duration_seconds",
        "item_count",
    )
    list_filter = ("source_type", "trust_type", "region", "language", "enabled")
    search_fields = ("name", "slug", "url", "last_error")
    readonly_fields = (
        "created_at",
        "updated_at",
        "last_checked_at",
        "last_success_at",
        "last_new_items_count",
        "last_fetch_duration_seconds",
        "average_fetch_duration_seconds",
    )
    prepopulated_fields = {"slug": ("name",)}

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(item_total=Count("news_items"))

    @admin.display(description="items")
    def item_count(self, obj: Source) -> int:
        return obj.item_total


@admin.register(RawItem)
class RawItemAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "source",
        "published_at",
        "published_at_precision",
        "date_confidence",
        "is_date_suspect",
        "rejection_reason",
        "extraction_confidence",
        "first_seen_at",
        "canonical_url",
    )
    list_filter = ("source", "published_at", "published_at_precision", "date_confidence", "is_date_suspect", "rejection_reason", "first_seen_at")
    search_fields = ("title", "url", "canonical_url", "source__name", "raw_text", "rejection_reason")
    readonly_fields = ("first_seen_at", "collected_at", "content_hash", "canonical_url_hash")
    date_hierarchy = "published_at"


class NewsItemFranchiseInline(admin.TabularInline):
    model = NewsItemFranchise
    extra = 0
    readonly_fields = ("matched_alias", "confidence_score")
    fields = ("franchise", "matched_alias", "confidence_score", "is_primary")


class NewsItemIssueInline(admin.TabularInline):
    model = NewsItemIssue
    extra = 0
    readonly_fields = ("relation_confidence", "explanation")


@admin.register(NewsItem)
class NewsItemAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "source",
        "trust_label",
        "category",
        "importance_score",
        "extraction_confidence",
        "date_confidence",
        "is_date_suspect",
        "is_backfill",
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
        "is_backfill",
        "date_confidence",
        "is_date_suspect",
        "importance_score",
    )
    search_fields = ("title", "url", "canonical_url", "source__name", "summary_ko")
    readonly_fields = (
        "created_at",
        "updated_at",
        "normalized_title",
        "canonical_url_hash",
        "importance_reasons",
        "trust_reasons",
        "entity_mentions",
        "date_suspect_reason",
    )
    date_hierarchy = "published_at"
    inlines = [NewsItemFranchiseInline, NewsItemIssueInline]


@admin.register(Issue)
class IssueAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "confidence_score", "first_seen_at", "last_updated_at", "official_confirmed_at")
    list_filter = ("status",)
    search_fields = ("title", "canonical_topic")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "last_updated_at"
    inlines = [NewsItemIssueInline]
    actions = ["merge_selected_issues", "mark_confirmed", "mark_debunked", "mark_stale"]

    @admin.action(description="선택한 이슈를 가장 오래된 이슈로 병합")
    def merge_selected_issues(self, request, queryset):
        issues = list(queryset.order_by("created_at", "pk"))
        if len(issues) < 2:
            self.message_user(request, "병합하려면 이슈를 2개 이상 선택하세요.", level=messages.WARNING)
            return

        primary = issues[0]
        with transaction.atomic():
            for issue in issues[1:]:
                for link in issue.news_links.select_related("news_item"):
                    NewsItemIssue.objects.get_or_create(
                        news_item=link.news_item,
                        issue=primary,
                        defaults={
                            "relation": link.relation,
                            "relation_confidence": link.relation_confidence,
                            "explanation": link.explanation,
                        },
                    )

                primary.status = _higher_issue_status(primary.status, issue.status)
                primary.confidence_score = max(primary.confidence_score, issue.confidence_score)
                primary.first_seen_at = min(primary.first_seen_at, issue.first_seen_at)
                primary.last_updated_at = max(primary.last_updated_at, issue.last_updated_at)
                if issue.official_confirmed_at:
                    if primary.official_confirmed_at is None:
                        primary.official_confirmed_at = issue.official_confirmed_at
                    else:
                        primary.official_confirmed_at = min(primary.official_confirmed_at, issue.official_confirmed_at)
                issue.delete()

            primary.save(
                update_fields=[
                    "status",
                    "confidence_score",
                    "first_seen_at",
                    "last_updated_at",
                    "official_confirmed_at",
                    "updated_at",
                ]
            )

        self.message_user(request, f"{len(issues)}개 이슈를 '{primary.title}' 이슈로 병합했습니다.")

    @admin.action(description="선택한 이슈를 공식 확정으로 표시")
    def mark_confirmed(self, request, queryset):
        now = timezone.now()
        updated = 0
        for issue in queryset:
            issue.status = IssueStatus.CONFIRMED
            if issue.official_confirmed_at is None:
                issue.official_confirmed_at = now
            issue.save(update_fields=["status", "official_confirmed_at", "updated_at"])
            updated += 1
        self.message_user(request, f"{updated}개 이슈를 공식 확정으로 표시했습니다.")

    @admin.action(description="선택한 이슈를 반박됨으로 표시")
    def mark_debunked(self, request, queryset):
        updated = queryset.update(status=IssueStatus.DEBUNKED, updated_at=timezone.now())
        self.message_user(request, f"{updated}개 이슈를 반박됨으로 표시했습니다.")

    @admin.action(description="선택한 이슈를 오래됨으로 표시")
    def mark_stale(self, request, queryset):
        updated = queryset.update(status=IssueStatus.STALE, updated_at=timezone.now())
        self.message_user(request, f"{updated}개 이슈를 오래됨으로 표시했습니다.")


def _higher_issue_status(left: str, right: str) -> str:
    left_score = ISSUE_STATUS_PRECEDENCE.get(left, 0)
    right_score = ISSUE_STATUS_PRECEDENCE.get(right, 0)
    return right if right_score > left_score else left


@admin.register(Franchise)
class FranchiseAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "priority")
    search_fields = ("name", "slug", "aliases")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(UserFranchiseFavorite)
class UserFranchiseFavoriteAdmin(admin.ModelAdmin):
    list_display = ("user", "franchise", "created_at")
    list_filter = ("franchise",)
    search_fields = ("user__username", "franchise__name", "franchise__slug")
    readonly_fields = ("created_at",)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("news_item", "channel", "status", "sent_at")
    list_filter = ("channel", "status")
    search_fields = ("news_item__title", "error")
    readonly_fields = ("sent_at",)
