from __future__ import annotations

from django.test import TestCase
from django.utils import timezone

from news.models import Source, SourceType, TrustType
from news.services.quality import article_rejection_reason, is_hub_url


class UrlRejectionTests(TestCase):
    def setUp(self) -> None:
        self.vgc = Source.objects.create(
            name="VGC Nintendo",
            slug="vgc-nintendo",
            url="https://www.videogameschronicle.com/platforms/nintendo/",
            source_type=SourceType.HTML,
            trust_type=TrustType.PRESS,
            config={
                "url_include_patterns": ["/news/", "/features/"],
                "url_exclude_patterns": ["/feed", "/rss", "/category/", "/tag/", "/platforms/"],
            },
        )

    def test_vgc_platform_page_is_rejected(self):
        url = "https://www.videogameschronicle.com/platforms/nintendo/nintendo-64"

        self.assertTrue(is_hub_url(url, self.vgc))
        self.assertEqual(
            article_rejection_reason(
                title="Nintendo 64",
                url=url,
                raw_text="A platform landing page.",
                published_at=timezone.now(),
                source=self.vgc,
            ),
            "hub_url",
        )

    def test_common_hub_urls_are_rejected(self):
        rejected_urls = [
            "https://example.com/category/nintendo",
            "https://example.com/tag/switch-2",
            "https://example.com/news/rss",
            "https://example.com/all-games",
            "https://example.com/tips-and-tricks",
            "https://example.com/search?q=Switch+2",
            "https://example.com/news/page/2",
        ]

        for url in rejected_urls:
            with self.subTest(url=url):
                self.assertTrue(is_hub_url(url, self.vgc))

    def test_actual_article_url_is_accepted(self):
        reason = article_rejection_reason(
            title="You Can Watch YouTube On Switch 2 But It Is Not Pretty",
            url="https://www.videogameschronicle.com/news/you-can-watch-youtube-on-switch-2-but-it-isnt-pretty/",
            raw_text="A normal article body.",
            published_at=timezone.now(),
            source=self.vgc,
        )

        self.assertEqual(reason, "")
