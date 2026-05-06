from __future__ import annotations

from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from news.models import NewsItem, RawItem, Source, SourceType, TrustLabel, TrustType
from news.services.classifier import ClassificationResult
from news.services.collectors import process_raw_item
from news.services.summarizer import summarize_item
from news.services.text import create_content_hash


class KoreanSummarizerTests(TestCase):
    def setUp(self) -> None:
        self.source = Source.objects.create(
            name="Nintendo Life",
            slug="nintendo-life",
            url="https://example.com/feed",
            source_type=SourceType.RSS,
            trust_type=TrustType.PRESS,
        )
        self.classification = ClassificationResult(
            trust_label=TrustLabel.REPORTED,
            category="new_game",
            tags=["switch2", "new_game"],
            confidence_score=73,
            trust_reasons=["전문 매체 보도 출처"],
        )

    def test_rule_summary_always_uses_korean_four_line_format(self):
        summary = summarize_item(
            source=self.source,
            title="Nintendo Switch 2 firmware update detailed",
            raw_text="Nintendo Switch 2 firmware update adds new system settings and quality of life fixes.",
            classification=self.classification,
            provider="rules",
        )

        self.assertIn("무슨 일?:", summary)
        self.assertIn("왜 중요?:", summary)
        self.assertIn("확인 상태:", summary)
        self.assertIn("주의:", summary)
        self.assertNotIn("아직 상세 요약은 없지만", summary)

    @override_settings(
        SUMMARY_OPENAI_API_KEY="test-key",
        SUMMARY_OPENAI_MODEL="gpt-test",
        SUMMARY_TIMEOUT_SECONDS=5,
        SUMMARY_MAX_SOURCE_CHARS=3000,
    )
    @patch("news.services.summarizer.httpx.post")
    def test_openai_provider_uses_responses_api_output(self, mock_post):
        response = Mock()
        response.json.return_value = {
            "output_text": (
                "무슨 일?: Switch 2 관련 업데이트가 보도됐습니다.\n"
                "왜 중요?: 기기 사용성 변화와 연결됩니다.\n"
                "확인 상태: 전문 매체 보도 기준으로 확인된 내용입니다.\n"
                "주의: 세부 내용은 원문 링크에서 확인하세요."
            )
        }
        mock_post.return_value = response

        summary = summarize_item(
            source=self.source,
            title="Switch 2 update reportedly improves YouTube playback",
            raw_text="A report says a Switch 2 update improves YouTube playback.",
            classification=self.classification,
            provider="openai",
        )

        self.assertIn("Switch 2 관련 업데이트", summary)
        request = mock_post.call_args.kwargs
        self.assertEqual(request["json"]["model"], "gpt-test")
        self.assertEqual(mock_post.call_args.args[0], "https://api.openai.com/v1/responses")

    @override_settings(SUMMARY_OPENAI_API_KEY="", SUMMARY_OPENAI_MODEL="gpt-test")
    def test_openai_provider_falls_back_to_rules_without_key(self):
        with self.assertLogs("news.services.summarizer", level="WARNING"):
            summary = summarize_item(
                source=self.source,
                title="Switch 2 rumor gains traction",
                raw_text="A forum rumor claims Switch 2 details are being discussed.",
                classification=self.classification,
                provider="openai",
            )

        self.assertIn("무슨 일?:", summary)
        self.assertIn("왜 중요?:", summary)


class SummarizeItemsCommandTests(TestCase):
    def setUp(self) -> None:
        self.source = Source.objects.create(
            name="Nintendo UK News",
            slug="nintendo-uk",
            url="https://example.com/feed",
            source_type=SourceType.RSS,
            trust_type=TrustType.OFFICIAL,
        )

    def make_item(self, title: str = "Nintendo Switch 2 update announced") -> NewsItem:
        raw = RawItem.objects.create(
            source=self.source,
            title=title,
            url="https://example.com/news/switch-2-update-announced",
            canonical_url="https://example.com/news/switch-2-update-announced",
            published_at=timezone.now(),
            raw_text="Nintendo announced a Switch 2 update with new system features.",
            content_hash=create_content_hash(title, "https://example.com/news/switch-2-update-announced"),
        )
        item, _created = process_raw_item(raw)
        return item

    def test_command_generates_missing_summary(self):
        item = self.make_item()
        NewsItem.objects.filter(pk=item.pk).update(summary_ko="")

        out = StringIO()
        call_command("summarize_items", "--limit", "1", stdout=out)

        item.refresh_from_db()
        self.assertIn("무슨 일?:", item.summary_ko)
        self.assertIn("updated=1", out.getvalue())

    def test_command_force_regenerates_existing_summary(self):
        item = self.make_item()
        NewsItem.objects.filter(pk=item.pk).update(summary_ko="기존 요약입니다.")

        out = StringIO()
        call_command("summarize_items", "--item", str(item.pk), "--force", "--provider", "rules", stdout=out)

        item.refresh_from_db()
        self.assertIn("무슨 일?:", item.summary_ko)
        self.assertIn("updated=1", out.getvalue())
