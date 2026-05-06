from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class SourceType(models.TextChoices):
    RSS = "rss", "RSS/Atom"
    HTML = "html", "HTML"
    YOUTUBE_RSS = "youtube_rss", "YouTube RSS"
    REDDIT_RSS = "reddit_rss", "Reddit RSS"
    GOOGLE_ALERT_RSS = "google_alert_rss", "Google Alert RSS"


class TrustType(models.TextChoices):
    OFFICIAL = "official", "Official"
    PRESS = "press", "Press"
    RUMOR = "rumor", "Rumor"
    AGGREGATOR = "aggregator", "Aggregator"
    UNKNOWN = "unknown", "Unknown"


class Region(models.TextChoices):
    KR = "KR", "Korea"
    JP = "JP", "Japan"
    US = "US", "United States"
    EU = "EU", "Europe"
    GLOBAL = "GLOBAL", "Global"


class Language(models.TextChoices):
    KO = "ko", "Korean"
    JA = "ja", "Japanese"
    EN = "en", "English"
    MULTI = "multi", "Multiple"


class TrustLabel(models.TextChoices):
    OFFICIAL = "official", "Official"
    REPORTED = "reported", "Reported"
    RUMOR = "rumor", "Rumor"
    UNKNOWN = "unknown", "Unknown"


class NewsCategory(models.TextChoices):
    OFFICIAL = "official", "Official"
    DIRECT = "direct", "Direct"
    RELEASE_DATE = "release_date", "Release Date"
    TRAILER = "trailer", "Trailer"
    NEW_GAME = "new_game", "New Game"
    UPDATE = "update", "Update"
    SALE = "sale", "Sale"
    RUMOR = "rumor", "Rumor"
    LEAK = "leak", "Leak"
    GENERAL = "general", "General"


class IssueStatus(models.TextChoices):
    RUMOR = "rumor", "Rumor"
    DEVELOPING = "developing", "Developing"
    CONFIRMED = "confirmed", "Confirmed"
    DEBUNKED = "debunked", "Debunked"
    STALE = "stale", "Stale"


class IssueRelation(models.TextChoices):
    SAME_STORY = "same_story", "Same story"
    FOLLOWUP = "followup", "Follow-up"
    CONFIRMATION = "confirmation", "Confirmation"
    DEBUNK = "debunk", "Debunk"
    RELATED = "related", "Related"


class PublishedAtPrecision(models.TextChoices):
    EXACT = "exact", "Exact"
    DATE_ONLY = "date_only", "Date only"
    UNKNOWN = "unknown", "Unknown"


class NotificationChannel(models.TextChoices):
    NTFY = "ntfy", "ntfy"
    DISCORD = "discord", "Discord"
    EMAIL = "email", "Email"
    NONE = "none", "None"


class NotificationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"


TRUST_BASE_SCORE = {
    TrustType.OFFICIAL: 50,
    TrustType.PRESS: 25,
    TrustType.RUMOR: 10,
    TrustType.AGGREGATOR: 15,
    TrustType.UNKNOWN: 5,
}


class Source(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    url = models.URLField(max_length=1000, blank=True)
    source_type = models.CharField(max_length=32, choices=SourceType.choices)
    trust_type = models.CharField(max_length=32, choices=TrustType.choices, default=TrustType.UNKNOWN)
    region = models.CharField(max_length=16, choices=Region.choices, default=Region.GLOBAL)
    language = models.CharField(max_length=16, choices=Language.choices, default=Language.MULTI)
    base_score = models.IntegerField(default=0)
    enabled = models.BooleanField(default=True)
    poll_interval_minutes = models.PositiveIntegerField(default=60)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    last_new_items_count = models.PositiveIntegerField(default=0)
    last_fetch_duration_seconds = models.FloatField(null=True, blank=True)
    average_fetch_duration_seconds = models.FloatField(null=True, blank=True)
    config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["enabled", "source_type"]),
            models.Index(fields=["trust_type", "region"]),
        ]

    def save(self, *args, **kwargs) -> None:
        if not self.base_score:
            self.base_score = TRUST_BASE_SCORE.get(self.trust_type, 5)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name

    @property
    def trust_type_ko(self) -> str:
        return {
            TrustType.OFFICIAL: "공식",
            TrustType.PRESS: "매체",
            TrustType.RUMOR: "루머",
            TrustType.AGGREGATOR: "집계",
            TrustType.UNKNOWN: "미확인",
        }.get(self.trust_type, "미확인")


class RawItem(models.Model):
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="raw_items")
    title = models.CharField(max_length=500)
    url = models.URLField(max_length=1000)
    canonical_url = models.URLField(max_length=1000, null=True, blank=True)
    author = models.CharField(max_length=255, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    raw_published_at_text = models.CharField(max_length=255, blank=True)
    published_at_precision = models.CharField(
        max_length=32,
        choices=PublishedAtPrecision.choices,
        default=PublishedAtPrecision.UNKNOWN,
    )
    first_seen_at = models.DateTimeField(default=timezone.now)
    collected_at = models.DateTimeField(default=timezone.now)
    raw_html = models.TextField(blank=True)
    raw_text = models.TextField(blank=True)
    content_hash = models.CharField(max_length=64, db_index=True)
    canonical_url_hash = models.CharField(max_length=64, blank=True, db_index=True)
    extraction_confidence = models.PositiveSmallIntegerField(default=100)
    rejection_reason = models.CharField(max_length=80, blank=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-published_at", "-first_seen_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["canonical_url"],
                condition=Q(canonical_url__isnull=False) & ~Q(canonical_url=""),
                name="unique_rawitem_canonical_url",
            ),
        ]
        indexes = [
            models.Index(fields=["published_at"]),
            models.Index(fields=["first_seen_at"]),
            models.Index(fields=["source"]),
            models.Index(fields=["content_hash"]),
            models.Index(fields=["canonical_url_hash"]),
            models.Index(fields=["rejection_reason"]),
        ]

    def __str__(self) -> str:
        return self.title


class NewsItem(models.Model):
    raw_item = models.OneToOneField(RawItem, on_delete=models.CASCADE, related_name="news_item")
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="news_items")
    title = models.CharField(max_length=500)
    normalized_title = models.CharField(max_length=500, db_index=True)
    url = models.URLField(max_length=1000)
    canonical_url = models.URLField(max_length=1000, null=True, blank=True)
    summary_ko = models.TextField(blank=True)
    summary_original = models.TextField(blank=True)
    trust_label = models.CharField(max_length=32, choices=TrustLabel.choices, default=TrustLabel.UNKNOWN)
    category = models.CharField(max_length=32, choices=NewsCategory.choices, default=NewsCategory.GENERAL)
    detected_tags = models.JSONField(default=list, blank=True)
    confidence_score = models.IntegerField(default=50)
    importance_score = models.IntegerField(default=0)
    region = models.CharField(max_length=16, choices=Region.choices, default=Region.GLOBAL)
    language = models.CharField(max_length=16, choices=Language.choices, default=Language.MULTI)
    published_at = models.DateTimeField(null=True, blank=True)
    published_at_precision = models.CharField(
        max_length=32,
        choices=PublishedAtPrecision.choices,
        default=PublishedAtPrecision.UNKNOWN,
    )
    first_seen_at = models.DateTimeField(default=timezone.now)
    is_backfill = models.BooleanField(default=False)
    canonical_url_hash = models.CharField(max_length=64, blank=True, db_index=True)
    extraction_confidence = models.PositiveSmallIntegerField(default=100)
    importance_reasons = models.JSONField(default=list, blank=True)
    thumbnail_url = models.URLField(max_length=1000, blank=True)
    is_read = models.BooleanField(default=False)
    is_bookmarked = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-first_seen_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["canonical_url"],
                condition=Q(canonical_url__isnull=False) & ~Q(canonical_url=""),
                name="unique_newsitem_canonical_url",
            ),
        ]
        indexes = [
            models.Index(fields=["trust_label", "category"]),
            models.Index(fields=["importance_score"]),
            models.Index(fields=["is_read", "is_bookmarked", "is_archived"]),
            models.Index(fields=["published_at"]),
            models.Index(fields=["first_seen_at"]),
            models.Index(fields=["source"]),
            models.Index(fields=["canonical_url_hash"]),
            models.Index(fields=["extraction_confidence", "is_archived"]),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def trust_label_ko(self) -> str:
        return {
            TrustLabel.OFFICIAL: "공식",
            TrustLabel.REPORTED: "보도",
            TrustLabel.RUMOR: "루머",
            TrustLabel.UNKNOWN: "미확인",
        }.get(self.trust_label, "미확인")

    @property
    def category_ko(self) -> str:
        return {
            NewsCategory.OFFICIAL: "공식",
            NewsCategory.DIRECT: "다이렉트",
            NewsCategory.RELEASE_DATE: "발매일",
            NewsCategory.TRAILER: "트레일러",
            NewsCategory.NEW_GAME: "신작",
            NewsCategory.UPDATE: "업데이트",
            NewsCategory.SALE: "세일",
            NewsCategory.RUMOR: "루머",
            NewsCategory.LEAK: "유출",
            NewsCategory.GENERAL: "일반",
        }.get(self.category, "일반")


class Issue(models.Model):
    title = models.CharField(max_length=500)
    canonical_topic = models.CharField(max_length=500, db_index=True)
    status = models.CharField(max_length=32, choices=IssueStatus.choices, default=IssueStatus.DEVELOPING)
    confidence_score = models.IntegerField(default=50)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_updated_at = models.DateTimeField(default=timezone.now)
    official_confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_updated_at"]
        indexes = [
            models.Index(fields=["status", "last_updated_at"]),
            models.Index(fields=["first_seen_at"]),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def status_ko(self) -> str:
        return {
            IssueStatus.RUMOR: "루머 관찰 중",
            IssueStatus.DEVELOPING: "전개 중",
            IssueStatus.CONFIRMED: "공식 확정",
            IssueStatus.DEBUNKED: "반박됨",
            IssueStatus.STALE: "오래됨",
        }.get(self.status, "전개 중")


class NewsItemIssue(models.Model):
    news_item = models.ForeignKey(NewsItem, on_delete=models.CASCADE, related_name="issue_links")
    issue = models.ForeignKey(Issue, on_delete=models.CASCADE, related_name="news_links")
    relation = models.CharField(max_length=32, choices=IssueRelation.choices, default=IssueRelation.RELATED)
    relation_confidence = models.FloatField(default=0.0)
    explanation = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["news_item", "issue"], name="unique_newsitem_issue"),
        ]

    def __str__(self) -> str:
        return f"{self.news_item} -> {self.issue}"


class Franchise(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    aliases = models.JSONField(default=list, blank=True)
    priority = models.IntegerField(default=0)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class NewsItemFranchise(models.Model):
    news_item = models.ForeignKey(NewsItem, on_delete=models.CASCADE, related_name="franchise_links")
    franchise = models.ForeignKey(Franchise, on_delete=models.CASCADE, related_name="news_links")
    matched_alias = models.CharField(max_length=255, blank=True)
    confidence_score = models.PositiveSmallIntegerField(default=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["news_item", "franchise"], name="unique_newsitem_franchise"),
        ]

    def __str__(self) -> str:
        return f"{self.news_item} -> {self.franchise}"


class UserFranchiseFavorite(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="franchise_favorites")
    franchise = models.ForeignKey(Franchise, on_delete=models.CASCADE, related_name="user_favorites")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "franchise"], name="unique_user_franchise_favorite"),
        ]
        indexes = [
            models.Index(fields=["user", "franchise"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} -> {self.franchise}"


class Notification(models.Model):
    news_item = models.ForeignKey(NewsItem, on_delete=models.CASCADE, related_name="notifications")
    channel = models.CharField(max_length=32, choices=NotificationChannel.choices, default=NotificationChannel.NONE)
    sent_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=32, choices=NotificationStatus.choices, default=NotificationStatus.PENDING)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-sent_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["news_item", "channel"],
                condition=Q(status=NotificationStatus.SENT),
                name="unique_sent_notification_per_channel",
            ),
        ]
        indexes = [
            models.Index(fields=["channel", "status"]),
            models.Index(fields=["news_item"]),
        ]

    def __str__(self) -> str:
        return f"{self.channel}: {self.news_item}"
