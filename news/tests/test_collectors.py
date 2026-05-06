from __future__ import annotations

from unittest.mock import patch

import httpx
from django.test import TestCase

from news.models import RawItem, Source, SourceType, TrustType
from news.services.collectors import collect_source


def response_for(url: str, body: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=body.encode("utf-8"),
        request=httpx.Request("GET", url),
    )


RSS_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Mock Feed</title>
    <item>
      <title>Nintendo Direct announced for Switch 2</title>
      <link>https://example.com/news/direct?utm_source=test</link>
      <pubDate>Wed, 06 May 2026 10:00:00 GMT</pubDate>
      <author>Editor</author>
      <description><![CDATA[<p>A new Direct was announced.</p>]]></description>
    </item>
  </channel>
</rss>
"""


class CollectorHttpTests(TestCase):
    def test_rss_collects_raw_item_and_updates_source_health(self):
        source = Source.objects.create(
            name="Mock RSS",
            slug="mock-rss",
            url="https://example.com/feed",
            source_type=SourceType.RSS,
            trust_type=TrustType.PRESS,
        )

        with patch("news.services.collectors.httpx.get", return_value=response_for(source.url, RSS_BODY)) as get:
            result = collect_source(source, limit=5)

        source.refresh_from_db()
        self.assertEqual(result.created_count, 1)
        self.assertEqual(result.duplicate_count, 0)
        self.assertEqual(source.last_new_items_count, 1)
        self.assertIsNotNone(source.last_checked_at)
        self.assertIsNotNone(source.last_success_at)
        self.assertEqual(source.last_error, "")
        self.assertEqual(RawItem.objects.get().canonical_url, "https://example.com/news/direct")
        self.assertIn("User-Agent", get.call_args.kwargs["headers"])

    def test_rss_duplicate_does_not_create_again(self):
        source = Source.objects.create(
            name="Mock RSS",
            slug="mock-rss",
            url="https://example.com/feed",
            source_type=SourceType.RSS,
            trust_type=TrustType.PRESS,
        )

        with patch("news.services.collectors.httpx.get", return_value=response_for(source.url, RSS_BODY)):
            first = collect_source(source)
            second = collect_source(source)

        source.refresh_from_db()
        self.assertEqual(first.created_count, 1)
        self.assertEqual(second.created_count, 0)
        self.assertEqual(second.duplicate_count, 1)
        self.assertEqual(source.last_new_items_count, 0)
        self.assertEqual(RawItem.objects.count(), 1)

    def test_youtube_rss_builds_feed_url_from_channel_id(self):
        source = Source.objects.create(
            name="YouTube",
            slug="youtube",
            source_type=SourceType.YOUTUBE_RSS,
            trust_type=TrustType.OFFICIAL,
            config={"channel_id": "UC123"},
        )

        with patch("news.services.collectors.httpx.get", return_value=response_for("https://www.youtube.com/feeds/videos.xml?channel_id=UC123", RSS_BODY)) as get:
            result = collect_source(source, limit=1)

        self.assertEqual(result.created_count, 1)
        self.assertEqual(get.call_args.args[0], "https://www.youtube.com/feeds/videos.xml?channel_id=UC123")

    def test_reddit_rss_uses_rss_collector(self):
        source = Source.objects.create(
            name="Reddit",
            slug="reddit",
            url="https://www.reddit.com/r/GamingLeaksAndRumours/search.rss?q=Nintendo",
            source_type=SourceType.REDDIT_RSS,
            trust_type=TrustType.RUMOR,
        )

        with patch("news.services.collectors.httpx.get", return_value=response_for(source.url, RSS_BODY)):
            result = collect_source(source)

        self.assertEqual(result.created_count, 1)
        self.assertEqual(RawItem.objects.get().source, source)

    def test_html_selector_config_extracts_item_fields(self):
        html = """
        <html><body>
          <article class="news-card">
            <h2><a href="/news/switch-2-direct">Switch 2 Direct details</a></h2>
            <time datetime="2026-05-06T09:00:00+09:00">May 6</time>
            <span class="byline">Nintendo Watch Desk</span>
            <p class="summary">A carefully mocked article summary.</p>
            <img src="/images/thumb.jpg" alt="">
          </article>
        </body></html>
        """
        source = Source.objects.create(
            name="HTML",
            slug="html",
            url="https://example.com/news",
            source_type=SourceType.HTML,
            trust_type=TrustType.OFFICIAL,
            config={
                "item_selector": "article.news-card",
                "title_selector": "h2 a",
                "link_selector": "h2 a",
                "date_selector": "time",
                "date_attr": "datetime",
                "summary_selector": ".summary",
                "author_selector": ".byline",
                "thumbnail_selector": "img",
                "thumbnail_attr": "src",
            },
        )

        with patch("news.services.collectors.httpx.get", return_value=response_for(source.url, html)):
            result = collect_source(source)

        raw = RawItem.objects.get()
        self.assertEqual(result.created_count, 1)
        self.assertEqual(raw.title, "Switch 2 Direct details")
        self.assertEqual(raw.canonical_url, "https://example.com/news/switch-2-direct")
        self.assertEqual(raw.author, "Nintendo Watch Desk")
        self.assertIn("mocked article summary", raw.raw_text)
        self.assertEqual(raw.metadata["thumbnail_url"], "https://example.com/images/thumb.jpg")

    def test_broken_source_sets_last_error_without_raising(self):
        source = Source.objects.create(
            name="Broken",
            slug="broken",
            url="https://example.com/feed",
            source_type=SourceType.RSS,
            trust_type=TrustType.PRESS,
        )

        with patch("news.services.collectors.httpx.get", return_value=response_for(source.url, "nope", status_code=500)):
            with self.assertLogs("news.services.collectors", level="ERROR"):
                result = collect_source(source)

        source.refresh_from_db()
        self.assertEqual(result.created_count, 0)
        self.assertEqual(source.last_new_items_count, 0)
        self.assertIn("500", source.last_error)
