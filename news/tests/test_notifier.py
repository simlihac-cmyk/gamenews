from __future__ import annotations

from unittest.mock import patch

import httpx
from django.test import TestCase, override_settings

from news.models import (
    NewsCategory,
    NewsItem,
    Notification,
    NotificationChannel,
    NotificationStatus,
    RawItem,
    Source,
    SourceType,
    TrustLabel,
    TrustType,
)
from news.services.notifier import format_korean_message, notify_if_needed
from news.services.text import create_content_hash, normalize_title


def ok_response(url: str = "https://example.com/webhook") -> httpx.Response:
    return httpx.Response(status_code=204, request=httpx.Request("POST", url))


class NotifierTests(TestCase):
    def make_item(self, *, importance: int = 90) -> NewsItem:
        source = Source.objects.create(
            name="한국닌텐도",
            slug=f"source-{importance}",
            url="https://example.com/feed",
            source_type=SourceType.RSS,
            trust_type=TrustType.OFFICIAL,
        )
        raw_item = RawItem.objects.create(
            source=source,
            title=f"Nintendo Direct 테스트 {importance}",
            url=f"https://example.com/news/{importance}",
            canonical_url=f"https://example.com/news/{importance}",
            raw_text="테스트 본문입니다.",
            content_hash=create_content_hash(f"Nintendo Direct 테스트 {importance}", f"https://example.com/news/{importance}"),
        )
        return NewsItem.objects.create(
            raw_item=raw_item,
            source=source,
            title=raw_item.title,
            normalized_title=normalize_title(raw_item.title),
            url=raw_item.url,
            canonical_url=raw_item.canonical_url,
            summary_ko="공식 출처에서 확인된 소식입니다. 닌텐도 다이렉트 관련 테스트 요약입니다.",
            trust_label=TrustLabel.OFFICIAL,
            category=NewsCategory.DIRECT,
            detected_tags=["direct", "switch2"],
            importance_score=importance,
            region=source.region,
            language=source.language,
        )

    @override_settings(NOTIFICATIONS_ENABLED=False)
    def test_notifications_disabled_creates_skip_record(self):
        item = self.make_item()

        with patch("news.services.notifier.httpx.post") as post:
            notifications = notify_if_needed(item)

        self.assertFalse(post.called)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].channel, NotificationChannel.NONE)
        self.assertEqual(notifications[0].status, NotificationStatus.SKIPPED)
        self.assertIn("disabled", notifications[0].error)

    @override_settings(NOTIFICATIONS_ENABLED=True, NOTIFICATION_MIN_IMPORTANCE=80, NTFY_TOPIC="topic", DISCORD_WEBHOOK_URL="")
    def test_below_threshold_skips_without_send(self):
        item = self.make_item(importance=30)

        with patch("news.services.notifier.httpx.post") as post:
            notifications = notify_if_needed(item)

        self.assertFalse(post.called)
        self.assertEqual(notifications[0].status, NotificationStatus.SKIPPED)
        self.assertIn("importance below", notifications[0].error)

    @override_settings(
        NOTIFICATIONS_ENABLED=True,
        NOTIFICATION_MIN_IMPORTANCE=80,
        NTFY_SERVER="https://ntfy.example.test",
        NTFY_TOPIC="nintendo-watch",
        DISCORD_WEBHOOK_URL="",
    )
    def test_ntfy_send_records_attempt_and_formats_korean_message(self):
        item = self.make_item()

        with patch("news.services.notifier.httpx.post", return_value=ok_response("https://ntfy.example.test/nintendo-watch")) as post:
            notifications = notify_if_needed(item)

        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].channel, NotificationChannel.NTFY)
        self.assertEqual(notifications[0].status, NotificationStatus.SENT)
        self.assertIsNotNone(notifications[0].sent_at)
        self.assertEqual(post.call_args.args[0], "https://ntfy.example.test/nintendo-watch")
        body = post.call_args.kwargs["content"].decode("utf-8")
        self.assertIn("제목:", body)
        self.assertIn("신뢰도: 공식", body)
        self.assertIn("카테고리: 다이렉트", body)
        self.assertIn("중요도: 90", body)
        self.assertIn("원문:", body)

    @override_settings(
        NOTIFICATIONS_ENABLED=True,
        NOTIFICATION_MIN_IMPORTANCE=80,
        NTFY_TOPIC="",
        DISCORD_WEBHOOK_URL="https://discord.example.test/webhook",
    )
    def test_discord_send_records_attempt(self):
        item = self.make_item()

        with patch("news.services.notifier.httpx.post", return_value=ok_response()) as post:
            notifications = notify_if_needed(item)

        self.assertEqual(notifications[0].channel, NotificationChannel.DISCORD)
        self.assertEqual(notifications[0].status, NotificationStatus.SENT)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["embeds"][0]["fields"][0]["value"], "공식")
        self.assertEqual(payload["embeds"][0]["fields"][1]["value"], "다이렉트")

    @override_settings(
        NOTIFICATIONS_ENABLED=True,
        NOTIFICATION_MIN_IMPORTANCE=80,
        NTFY_TOPIC="",
        DISCORD_WEBHOOK_URL="https://discord.example.test/webhook",
    )
    def test_discord_failure_records_failed_attempt_without_raising(self):
        item = self.make_item()

        with patch("news.services.notifier.httpx.post", side_effect=httpx.TimeoutException("timeout")):
            notifications = notify_if_needed(item)

        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].channel, NotificationChannel.DISCORD)
        self.assertEqual(notifications[0].status, NotificationStatus.FAILED)
        self.assertIn("timeout", notifications[0].error)

    @override_settings(
        NOTIFICATIONS_ENABLED=True,
        NOTIFICATION_MIN_IMPORTANCE=80,
        NTFY_SERVER="https://ntfy.example.test",
        NTFY_TOPIC="nintendo-watch",
        DISCORD_WEBHOOK_URL="",
    )
    def test_duplicate_notifications_are_not_sent_twice(self):
        item = self.make_item()

        with patch("news.services.notifier.httpx.post", return_value=ok_response()) as post:
            first = notify_if_needed(item)
            second = notify_if_needed(item)

        self.assertEqual(post.call_count, 1)
        self.assertEqual(first[0].pk, second[0].pk)
        self.assertEqual(Notification.objects.filter(channel=NotificationChannel.NTFY, status=NotificationStatus.SENT).count(), 1)

    def test_format_korean_message_contains_required_fields(self):
        item = self.make_item()

        message = format_korean_message(item)

        self.assertIn("제목:", message)
        self.assertIn("신뢰도:", message)
        self.assertIn("카테고리:", message)
        self.assertIn("중요도:", message)
        self.assertIn("요약:", message)
        self.assertIn("원문:", message)
