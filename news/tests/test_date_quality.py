from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from news.models import NewsItem, RawItem, Source, SourceType, TrustType
from news.services.collectors import process_raw_item
from news.services.text import create_content_hash


class DateQualityTests(TestCase):
    def setUp(self) -> None:
        self.source = Source.objects.create(
            name="Nintendo Test",
            slug="nintendo-test",
            url="https://example.com/feed",
            source_type=SourceType.RSS,
            trust_type=TrustType.OFFICIAL,
        )

    def make_raw(self, title: str, url: str, *, published_at) -> RawItem:
        return RawItem.objects.create(
            source=self.source,
            title=title,
            url=url,
            canonical_url=url,
            published_at=published_at,
            raw_text="Nintendo announced a Switch 2 update.",
            content_hash=create_content_hash(title, url),
        )

    def make_item(self, title: str, *, published_at=None):
        published_at = published_at or timezone.now()
        raw = self.make_raw(title, f"https://example.com/news/{title.lower().replace(' ', '-')}", published_at=published_at)
        item, _created = process_raw_item(raw)
        return item

    def test_future_2064_item_is_hidden_from_default_and_today_filter(self):
        today_item = self.make_item("Normal Switch 2 news today")
        future = timezone.datetime(2064, 5, 6, 12, tzinfo=timezone.get_current_timezone())
        future_item = self.make_item("Nintendo 64 future dated item")
        RawItem.objects.filter(pk=future_item.raw_item_id).update(published_at=future, rejection_reason="", is_date_suspect=False)
        NewsItem.objects.filter(pk=future_item.pk).update(published_at=future, is_date_suspect=False, is_archived=False)

        default_response = self.client.get(reverse("news:item_list"))
        self.assertContains(default_response, today_item.title)
        self.assertNotContains(default_response, "Nintendo 64 future dated item")

        today_response = self.client.get(reverse("news:item_list"), {"date_from": timezone.localdate().isoformat()})
        self.assertContains(today_response, today_item.title)
        self.assertNotContains(today_response, "Nintendo 64 future dated item")

    def test_date_from_only_does_not_include_future_items(self):
        self.make_item("Normal Switch 2 news today")
        future = timezone.now() + timedelta(days=3)
        future_item = self.make_item("Future Switch 2 item")
        RawItem.objects.filter(pk=future_item.raw_item_id).update(published_at=future, rejection_reason="", is_date_suspect=False)
        NewsItem.objects.filter(pk=future_item.pk).update(published_at=future, is_date_suspect=False, is_archived=False)

        response = self.client.get(reverse("news:item_list"), {"date_from": timezone.localdate().isoformat()})

        self.assertNotContains(response, "Future Switch 2 item")

    def test_now_plus_25h_is_suspect_and_not_promoted(self):
        raw = self.make_raw(
            "Switch 2 future post",
            "https://example.com/news/switch-2-future-post",
            published_at=timezone.now() + timedelta(hours=25),
        )

        item, created = process_raw_item(raw)

        raw.refresh_from_db()
        self.assertIsNone(item)
        self.assertFalse(created)
        self.assertTrue(raw.is_date_suspect)
        self.assertEqual(raw.date_confidence, "low")
        self.assertEqual(raw.rejection_reason, "date_suspect")

    def test_current_year_plus_two_is_suspect(self):
        value = timezone.now().replace(year=timezone.now().year + 2)
        raw = self.make_raw(
            "Switch 2 impossible year",
            "https://example.com/news/switch-2-impossible-year",
            published_at=value,
        )

        process_raw_item(raw)

        raw.refresh_from_db()
        self.assertTrue(raw.is_date_suspect)
        self.assertEqual(raw.date_suspect_reason, "year_too_far_future")

    def test_normal_today_item_is_visible_in_today_filter(self):
        item = self.make_item("Normal Switch 2 news today")

        response = self.client.get(reverse("news:item_list"), {"date_from": timezone.localdate().isoformat()})

        self.assertContains(response, item.title)
