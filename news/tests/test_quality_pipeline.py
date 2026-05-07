from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from news.models import Franchise, Issue, IssueRelation, NewsContentType, NewsItem, RawItem, Source, SourceType, TrustType
from news.services.collectors import process_raw_item
from news.services.importance import calculate_importance, calculate_nintendo_relevance
from news.services.quality import clean_title
from news.services.text import create_content_hash


class QualityPipelineTests(TestCase):
    def setUp(self) -> None:
        self.source = Source.objects.create(
            name="Nintendo US",
            slug="nintendo-us",
            url="https://example.com/feed",
            source_type=SourceType.RSS,
            trust_type=TrustType.OFFICIAL,
        )
        self.kirby = Franchise.objects.create(name="Kirby", slug="kirby", aliases=["Kirby"], priority=80)

    def make_raw(self, title: str, url: str, *, published_at=None, raw_text: str = "") -> RawItem:
        return RawItem.objects.create(
            source=self.source,
            title=title,
            url=url,
            canonical_url=url,
            published_at=published_at,
            raw_text=raw_text,
            content_hash=create_content_hash(title, url),
        )

    def test_clean_title_removes_dates_and_read_more_tail(self):
        self.assertEqual(
            clean_title("04/30/26 Get the most out of Pokémon Pokopia Read more"),
            "Get the most out of Pokémon Pokopia",
        )
        self.assertEqual(
            clean_title("05/05/26 Game Pak Decor Pikmin arrive in Pikmin Bloom This month, the Pikmin™ Bloom game has new decor. Read more"),
            "Game Pak Decor Pikmin arrive in Pikmin Bloom",
        )
        self.assertEqual(clean_title("젤다의 전설 새 소식 뉴스 2026.4.30"), "젤다의 전설 새 소식")
        self.assertEqual(
            clean_title("The making of the music of Kirby Air Riders – Chapter 2"),
            "The making of the music of Kirby Air Riders – Chapter 2",
        )
        self.assertEqual(
            clean_title("13/04/2026 | Nintendo Switch 2 Kirby Air Riders: Development Insights with Masahiro Sakurai – Part 1 Air Ride and Top Ride"),
            "Kirby Air Riders: Development Insights with Masahiro Sakurai – Part 1 Air Ride and Top Ride",
        )
        self.assertEqual(
            clean_title("Nintendo eShop Highlights – 30/04/2026 30/04/2026 | Nintendo Switch Latest games"),
            "Nintendo eShop Highlights",
        )

    def test_long_or_body_like_title_is_marked_suspect_and_not_issue_title(self):
        raw = self.make_raw(
            "05/05/26 " + ("Very long Nintendo official title segment " * 4) + "Read more",
            "https://example.com/news/long-title",
            published_at=timezone.now(),
            raw_text="Nintendo official article body.",
        )

        item, _created = process_raw_item(raw)
        issue = item.issue_links.get().issue

        self.assertTrue(item.title_suspect)
        self.assertNotEqual(issue.title, item.title)
        self.assertNotIn("Read more", issue.title)

    def test_boilerplate_raw_item_is_not_promoted(self):
        raw = self.make_raw("Skip to main content", "https://example.com/news/navigation")

        news_item, created = process_raw_item(raw)

        raw.refresh_from_db()
        self.assertIsNone(news_item)
        self.assertFalse(created)
        self.assertEqual(raw.rejection_reason, "boilerplate_title")
        self.assertEqual(NewsItem.objects.count(), 0)

    def test_shop_all_boilerplate_records_reason(self):
        raw = self.make_raw("My Nintendo Store Shop all", "https://example.com/news/store")

        process_raw_item(raw)

        raw.refresh_from_db()
        self.assertEqual(raw.rejection_reason, "boilerplate_title")

    def test_raw_title_is_preserved_and_clean_title_is_public(self):
        raw = self.make_raw(
            "04/30/26 Get the most out of Pokémon Pokopia Read more",
            "https://example.com/news/pokemon-pokopia",
            published_at=timezone.now(),
        )

        news_item, created = process_raw_item(raw)

        self.assertTrue(created)
        self.assertEqual(raw.title, "04/30/26 Get the most out of Pokémon Pokopia Read more")
        self.assertEqual(news_item.title, "Get the most out of Pokémon Pokopia")
        self.assertEqual(news_item.raw_item.title, raw.title)

    def test_unknown_published_at_is_not_high_confidence_and_reasons_are_present(self):
        raw = self.make_raw(
            "Nintendo Switch 2 feature announced",
            "https://example.com/news/switch-2-feature",
            raw_text="Nintendo announced a Switch 2 feature.",
        )

        item, _created = process_raw_item(raw)

        raw.refresh_from_db()
        self.assertIsNone(item.published_at)
        self.assertNotEqual(item.date_confidence, "high")
        self.assertNotEqual(raw.date_confidence, "high")
        self.assertTrue(item.importance_reasons)
        self.assertTrue(item.trust_reasons)

    def test_audit_news_quality_dry_run_and_apply_quarantines_hub(self):
        raw = self.make_raw(
            "Nintendo 64",
            "https://example.com/platforms/nintendo/nintendo-64",
            published_at=timezone.now(),
            raw_text="A platform hub page.",
        )

        dry_out = StringIO()
        call_command("audit_news_quality", stdout=dry_out)
        raw.refresh_from_db()
        self.assertEqual(raw.rejection_reason, "")
        self.assertIn("would be quarantined", dry_out.getvalue())

        apply_out = StringIO()
        call_command("audit_news_quality", "--apply", stdout=apply_out)
        raw.refresh_from_db()
        self.assertEqual(raw.rejection_reason, "hub_url")
        self.assertIn("quarantined", apply_out.getvalue())

    def test_audit_news_quality_leaves_soft_title_cleanup_unchanged_by_default(self):
        raw = self.make_raw(
            "04/30/26 Get the most out of Pokémon Pokopia Read more",
            "https://example.com/news/pokemon-pokopia",
            published_at=timezone.now(),
        )

        out = StringIO()
        call_command("audit_news_quality", "--apply", stdout=out)

        raw.refresh_from_db()
        self.assertEqual(raw.rejection_reason, "")
        self.assertIn("0 item(s) quarantined", out.getvalue())
        self.assertIn("soft cleanup-only", out.getvalue())

    def test_audit_news_quality_apply_soft_can_quarantine_title_cleanup_findings(self):
        raw = self.make_raw(
            "04/30/26 Get the most out of Pokémon Pokopia Read more",
            "https://example.com/news/pokemon-pokopia",
            published_at=timezone.now(),
        )

        call_command("audit_news_quality", "--apply", "--apply-soft", stdout=StringIO())

        raw.refresh_from_db()
        self.assertEqual(raw.rejection_reason, "unclean_title")

    def test_audit_news_quality_reasons_filter_limits_output(self):
        hub = self.make_raw(
            "Nintendo 64",
            "https://example.com/platforms/nintendo/nintendo-64",
            published_at=timezone.now(),
        )
        self.make_raw(
            "04/30/26 Get the most out of Pokémon Pokopia Read more",
            "https://example.com/news/pokemon-pokopia",
            published_at=timezone.now(),
        )

        out = StringIO()
        call_command("audit_news_quality", "--reasons", "hub_url", stdout=out)

        output = out.getvalue()
        self.assertIn(str(hub.pk), output)
        self.assertIn("hub_url", output)
        self.assertNotIn("read_more_in_title", output)

    def test_boilerplate_importance_is_zero(self):
        score = calculate_importance(source=self.source, title="Characters hub")

        self.assertEqual(score, 0)

    def test_static_page_and_low_relevance_importance_are_penalized(self):
        static_score = calculate_importance(
            source=self.source,
            title="Nintendo Switch 2 software list",
            raw_text="All Nintendo Switch 2 software.",
            content_type=NewsContentType.LIST_PAGE,
            published_at=timezone.now(),
            nintendo_relevance_score=4,
        )
        press = Source(name="Press", slug="press", source_type=SourceType.RSS, trust_type=TrustType.PRESS)
        relevance = calculate_nintendo_relevance(
            source=press,
            title="PlayStation Studios lineup report",
            raw_text="A PlayStation focused industry report.",
        )
        low_relevance_score = calculate_importance(
            source=press,
            title="PlayStation Studios lineup report",
            raw_text="A PlayStation focused industry report.",
            published_at=timezone.now(),
            nintendo_relevance_score=relevance,
        )

        self.assertLess(static_score, 80)
        self.assertLessEqual(relevance, 1)
        self.assertLess(low_relevance_score, 40)

    def test_data_quality_commands_are_dry_run_by_default(self):
        raw = self.make_raw(
            "Nintendo Switch 2 feature announced",
            "https://example.com/news/switch-2-feature-command",
            published_at=timezone.now(),
            raw_text="Nintendo announced a Switch 2 feature.",
        )
        item, _created = process_raw_item(raw)
        NewsItem.objects.filter(pk=item.pk).update(importance_reasons=[], trust_reasons=[])

        audit_out = StringIO()
        call_command("audit_data_quality", "--dry-run", stdout=audit_out)
        self.assertIn("score_problem", audit_out.getvalue())

        recalc_out = StringIO()
        call_command("recalculate_scores", "--dry-run", "--item", str(item.pk), stdout=recalc_out)
        item.refresh_from_db()
        self.assertEqual(item.importance_reasons, [])
        self.assertIn("DRY-RUN", recalc_out.getvalue())

        apply_out = StringIO()
        call_command("recalculate_scores", "--apply", "--only-missing-reasons", "--item", str(item.pk), stdout=apply_out)
        item.refresh_from_db()
        self.assertTrue(item.importance_reasons)
        self.assertTrue(item.trust_reasons)
        self.assertIsInstance(item.importance_reasons[0], dict)

    def test_switch2_only_overlap_does_not_group_same_issue(self):
        first = self.make_raw(
            "Nintendo Switch 2 eShop Highlights",
            "https://example.com/news/switch-2-eshop-highlights",
            published_at=timezone.now(),
        )
        second = self.make_raw(
            "Nintendo Switch 2 system menu update arrives",
            "https://example.com/news/switch-2-menu-update",
            published_at=timezone.now(),
        )

        process_raw_item(first)
        process_raw_item(second)

        self.assertEqual(Issue.objects.count(), 2)

    def test_kirby_followups_group_as_same_story_with_explanation(self):
        first = self.make_raw(
            "The making of the music of Kirby Air Riders - Chapter 1",
            "https://example.com/news/kirby-air-riders-music-1",
            published_at=timezone.now(),
        )
        second = self.make_raw(
            "The making of the music of Kirby Air Riders - Chapter 2",
            "https://example.com/news/kirby-air-riders-music-2",
            published_at=timezone.now(),
        )

        process_raw_item(first)
        second_item, _created = process_raw_item(second)

        self.assertEqual(Issue.objects.count(), 1)
        link = second_item.issue_links.get()
        self.assertEqual(link.relation, IssueRelation.SAME_STORY)
        self.assertGreater(link.relation_confidence, 0)
        self.assertIn("overlap=", link.explanation)
