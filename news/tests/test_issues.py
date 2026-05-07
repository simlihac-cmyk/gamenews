from __future__ import annotations

from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from news.models import (
    Franchise,
    Issue,
    IssueRelation,
    IssueStatus,
    NewsItem,
    NewsItemIssue,
    RawItem,
    Source,
    SourceType,
    TrustType,
)
from news.services.collectors import process_raw_item
from news.services.text import create_content_hash


class IssueGroupingTests(TestCase):
    def setUp(self) -> None:
        self.rumor_source = Source.objects.create(
            name="Rumor Source",
            slug="rumor-source",
            url="https://rumor.example/feed",
            source_type=SourceType.RSS,
            trust_type=TrustType.RUMOR,
        )
        self.official_source = Source.objects.create(
            name="Official Source",
            slug="official-source",
            url="https://official.example/feed",
            source_type=SourceType.RSS,
            trust_type=TrustType.OFFICIAL,
        )
        self.zelda = Franchise.objects.create(
            name="Zelda",
            slug="zelda",
            aliases=["The Legend of Zelda", "젤다"],
            priority=100,
        )
        self.user = get_user_model().objects.create_user(username="tester", password="password")

    def make_raw_item(self, source: Source, title: str, url: str, raw_text: str = "") -> RawItem:
        return RawItem.objects.create(
            source=source,
            title=title,
            url=url,
            canonical_url=url,
            raw_text=raw_text,
            content_hash=create_content_hash(title, url),
        )

    def test_rumor_item_creates_rumor_issue(self):
        raw_item = self.make_raw_item(
            self.rumor_source,
            "Zelda remake leak reportedly planned",
            "https://rumor.example/zelda-remake-leak",
        )

        news_item, created = process_raw_item(raw_item)

        issue = Issue.objects.get()
        link = NewsItemIssue.objects.get(news_item=news_item, issue=issue)
        self.assertTrue(created)
        self.assertEqual(issue.status, IssueStatus.RUMOR)
        self.assertEqual(issue.status_ko, "루머 관찰 중")
        self.assertEqual(link.relation, IssueRelation.SAME_STORY)
        self.assertEqual(list(news_item.franchise_links.values_list("franchise", flat=True)), [self.zelda.pk])

    def test_official_item_confirms_related_rumor_issue(self):
        rumor_raw = self.make_raw_item(
            self.rumor_source,
            "Zelda remake leak reportedly planned",
            "https://rumor.example/zelda-remake-leak",
        )
        official_raw = self.make_raw_item(
            self.official_source,
            "The Legend of Zelda remake release date announced",
            "https://official.example/zelda-release-date",
            raw_text="Nintendo shared the release date in an official announcement.",
        )

        process_raw_item(rumor_raw)
        official_item, _created = process_raw_item(official_raw)

        issue = Issue.objects.get()
        official_link = NewsItemIssue.objects.get(news_item=official_item, issue=issue)
        self.assertEqual(Issue.objects.count(), 1)
        self.assertEqual(issue.status, IssueStatus.CONFIRMED)
        self.assertEqual(issue.status_ko, "공식 확정")
        self.assertIsNotNone(issue.official_confirmed_at)
        self.assertEqual(official_link.relation, IssueRelation.OFFICIAL_CONFIRMATION)

    def test_official_confirmation_needs_franchise_and_event_overlap(self):
        rumor_raw = self.make_raw_item(
            self.rumor_source,
            "Zelda remake leak",
            "https://rumor.example/zelda-remake",
        )
        official_raw = self.make_raw_item(
            self.official_source,
            "The Legend of Zelda remake launches next year",
            "https://official.example/zelda-launch",
            raw_text="An official release date window was announced.",
        )

        process_raw_item(rumor_raw)
        process_raw_item(official_raw)

        issue = Issue.objects.get()
        self.assertEqual(Issue.objects.count(), 1)
        self.assertEqual(issue.status, IssueStatus.CONFIRMED)

    def test_shared_franchise_only_does_not_confirm_same_story(self):
        rumor_raw = self.make_raw_item(
            self.rumor_source,
            "Zelda remake leak",
            "https://rumor.example/zelda-remake-broad",
        )
        official_raw = self.make_raw_item(
            self.official_source,
            "The Legend of Zelda launches next year",
            "https://official.example/zelda-launch-broad",
            raw_text="An official release date window was announced.",
        )

        process_raw_item(rumor_raw)
        process_raw_item(official_raw)

        self.assertEqual(Issue.objects.count(), 2)

    def test_mark_stale_issues_command_marks_old_open_issues(self):
        issue = Issue.objects.create(
            title="Old rumor",
            canonical_topic="old rumor",
            status=IssueStatus.RUMOR,
            confidence_score=45,
            first_seen_at=timezone.now() - timedelta(days=45),
            last_updated_at=timezone.now() - timedelta(days=45),
        )

        out = StringIO()
        call_command("mark_stale_issues", "--days", "30", stdout=out)

        issue.refresh_from_db()
        self.assertEqual(issue.status, IssueStatus.STALE)
        self.assertIn("1 issue(s) marked stale", out.getvalue())

    def test_archive_low_value_items_command_archives_old_read_items(self):
        raw_item = self.make_raw_item(
            self.rumor_source,
            "Small old rumor",
            "https://rumor.example/small-old-rumor",
        )
        news_item, _created = process_raw_item(raw_item)
        old_time = timezone.now() - timedelta(days=90)
        NewsItem.objects.filter(pk=news_item.pk).update(
            first_seen_at=old_time,
            published_at=old_time,
            importance_score=5,
            is_read=True,
        )

        out = StringIO()
        call_command("archive_low_value_items", "--days", "60", "--max-importance", "10", stdout=out)

        news_item.refresh_from_db()
        self.assertTrue(news_item.is_archived)
        self.assertIn("1 item(s) archived", out.getvalue())

    def test_issue_list_filters_by_status(self):
        Issue.objects.create(
            title="Confirmed issue",
            canonical_topic="confirmed issue",
            status=IssueStatus.CONFIRMED,
            confidence_score=90,
        )
        Issue.objects.create(
            title="Rumor issue",
            canonical_topic="rumor issue",
            status=IssueStatus.RUMOR,
            confidence_score=45,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("news:issue_list"), {"status": IssueStatus.CONFIRMED})

        self.assertContains(response, "Confirmed issue")
        self.assertNotContains(response, "Rumor issue")
        self.assertContains(response, "공식 확정")

    def test_item_list_shows_linked_issue_summary(self):
        raw_item = self.make_raw_item(
            self.rumor_source,
            "Zelda remake leak reportedly planned",
            "https://rumor.example/zelda-remake-leak",
        )
        news_item, _created = process_raw_item(raw_item)
        issue = news_item.issue_links.select_related("issue").get().issue
        self.client.force_login(self.user)

        response = self.client.get(reverse("news:item_list"))

        self.assertContains(response, issue.title)
        self.assertContains(response, "관련 1건")
        self.assertContains(response, "루머 관찰 중")

    def test_youtube_switch2_and_pikmin_bloom_do_not_merge(self):
        Franchise.objects.create(
            name="Pikmin",
            slug="pikmin",
            aliases=["Pikmin", "Pikmin Bloom"],
            priority=80,
        )
        pikmin_raw = self.make_raw_item(
            self.official_source,
            "Game Pak Decor Pikmin arrive in Pikmin Bloom",
            "https://official.example/pikmin-bloom-game-pak",
            raw_text="Pikmin Bloom receives a new decor event.",
        )
        youtube_raw = self.make_raw_item(
            self.official_source,
            "You Can Watch YouTube On Switch 2 But It Is Not Pretty",
            "https://official.example/youtube-on-switch-2",
            raw_text="The YouTube app experience on Switch 2 is rough.",
        )

        pikmin_item, _ = process_raw_item(pikmin_raw)
        youtube_item, _ = process_raw_item(youtube_raw)

        self.assertEqual(Issue.objects.count(), 2)
        self.assertNotEqual(
            pikmin_item.issue_links.get().issue_id,
            youtube_item.issue_links.get().issue_id,
        )

    def test_unrelated_official_items_do_not_merge_on_source_or_switch_only(self):
        Franchise.objects.create(name="Pikmin", slug="pikmin", aliases=["Pikmin", "Pikmin Bloom"], priority=80)
        Franchise.objects.create(name="Pokémon", slug="pokemon", aliases=["Pokémon", "Pokemon", "Pokopia"], priority=90)
        cases = [
            ("Game Pak Decor Pikmin arrive in Pikmin Bloom", "Pikmin Bloom receives a new decor event.", "pikmin"),
            ("Get ready for Pokémon Pokopia on Nintendo Switch 2", "A separate Pokemon title has a Switch 2 update.", "pokopia"),
            ("PRAGMATA launches on Nintendo Switch 2", "A Capcom game has Switch 2 platform notes.", "pragmata"),
        ]

        items = []
        for title, text, slug in cases:
            raw = self.make_raw_item(
                self.official_source,
                title,
                f"https://official.example/{slug}",
                raw_text=text,
            )
            item, _created = process_raw_item(raw)
            items.append(item)

        self.assertEqual(Issue.objects.count(), 3)
        self.assertEqual(len({item.issue_links.get().issue_id for item in items}), 3)

    def test_official_series_mixed_issue_is_marked_review_required(self):
        franchises = [
            Franchise.objects.create(name="Pikmin", slug="pikmin", aliases=["Pikmin", "Pikmin Bloom"], priority=80),
            Franchise.objects.create(name="Mario", slug="mario", aliases=["Mario", "Mario Kart"], priority=90),
            Franchise.objects.create(name="Kirby", slug="kirby", aliases=["Kirby", "Kirby Air Riders"], priority=80),
            Franchise.objects.create(name="Pokémon", slug="pokemon", aliases=["Pokemon", "Pokémon"], priority=90),
        ]
        issue = Issue.objects.create(
            title="13/04/2026 | Nintendo Switch 2 Kirby Air Riders Development Insights Read more",
            canonical_topic="mixed official cards",
            status=IssueStatus.CONFIRMED,
            confidence_score=93,
        )
        titles = [
            "Nintendo eShop Highlights – 30/04/2026 30/04/2026 | Nintendo Switch",
            "Kirby Air Riders: Development Insights with Masahiro Sakurai – Part 1 Air Ride and Top Ride",
            "Mario Kart World event announced for Nintendo Switch 2",
            "Pokémon Champions update arrives on Nintendo Switch",
            "Ask the Developer Vol. 18, Kirby Air Riders",
            "Pikmin Bloom Game Pak Decor event starts",
        ]
        for index in range(14):
            title = titles[index % len(titles)]
            raw = self.make_raw_item(
                self.official_source,
                title,
                f"https://official.example/mixed-series-{index}",
                raw_text=f"{title} separate official card.",
            )
            item, _created = process_raw_item(raw)
            item.issue_links.all().delete()
            franchise = franchises[index % len(franchises)]
            franchise_link, _created = item.franchise_links.get_or_create(
                franchise=franchise,
                defaults={"matched_alias": franchise.name, "confidence_score": 90, "is_primary": True},
            )
            if not franchise_link.is_primary:
                franchise_link.is_primary = True
                franchise_link.save(update_fields=["is_primary"])
            NewsItemIssue.objects.create(
                news_item=item,
                issue=issue,
                relation=IssueRelation.SAME_STORY,
                relation_confidence=0.9,
                explanation="same_story: shared_franchise=True, score=1.18",
                decision_debug={"strong_signals": [], "weak_signals": ["same_source"], "decision": "linked"},
            )

        from news.services.issues import refresh_issue_review_status

        refresh_issue_review_status(issue)
        issue.refresh_from_db()

        self.assertTrue(issue.review_required)
        self.assertTrue(any("mixed_official_series" in reason or reason.startswith("same_story_items=") for reason in issue.review_reasons))

    def test_breakdown_capcom_playstation_roundups_do_not_merge(self):
        press = Source.objects.create(
            name="Press Source",
            slug="press-source",
            url="https://press.example/feed",
            source_type=SourceType.RSS,
            trust_type=TrustType.PRESS,
        )
        titles = [
            ("The Nintendo Breakdown 4 rumor roundup", "A broad Nintendo Switch 2 rumor roundup mentions many series."),
            ("Capcom outlines every current PlayStation and PC project", "A Capcom industry article focused on PlayStation and PC."),
            ("PlayStation Studios full lineup report", "A PlayStation Studios article with no Nintendo platform focus."),
        ]
        items = []
        for index, (title, text) in enumerate(titles):
            raw = self.make_raw_item(press, title, f"https://press.example/breakdown-{index}", raw_text=text)
            item, _created = process_raw_item(raw)
            items.append(item)

        self.assertEqual(Issue.objects.count(), 3)
        self.assertEqual(len({item.issue_links.get().issue_id for item in items}), 3)

    def test_clustering_debug_is_saved_for_linked_same_story(self):
        first = self.make_raw_item(
            self.official_source,
            "The making of the music of Zelda Echoes - Chapter 1",
            "https://official.example/zelda-music-1",
        )
        second = self.make_raw_item(
            self.official_source,
            "The making of the music of Zelda Echoes - Chapter 2",
            "https://official.example/zelda-music-2",
        )

        process_raw_item(first)
        second_item, _created = process_raw_item(second)

        link = second_item.issue_links.get()
        self.assertEqual(link.relation, IssueRelation.SAME_STORY)
        self.assertTrue(link.decision_debug)
        self.assertIn("strong_signals", link.decision_debug)
        self.assertEqual(link.decision_debug["decision"], "linked")

    def test_mark_suspect_issues_dry_run_and_apply(self):
        franchises = [
            Franchise.objects.create(name=f"Series {index}", slug=f"series-{index}", aliases=[f"Series {index}"], priority=50)
            for index in range(5)
        ]
        issue = Issue.objects.create(
            title="Mixed issue Read more",
            canonical_topic="mixed issue",
            status=IssueStatus.CONFIRMED,
            confidence_score=93,
        )
        for index in range(12):
            franchise = franchises[index % len(franchises)]
            title = f"{franchise.name} separate topic {index}"
            raw = self.make_raw_item(
                self.official_source,
                title,
                f"https://official.example/mixed-{index}",
                raw_text=f"{franchise.name} has a separate announcement.",
            )
            item, _created = process_raw_item(raw)
            item.issue_links.all().delete()
            NewsItemIssue.objects.create(
                news_item=item,
                issue=issue,
                relation=IssueRelation.SAME_STORY,
                relation_confidence=0.9,
                explanation="test mixed issue",
                decision_debug={"strong_signals": ["test"], "weak_signals": [], "decision": "linked"},
            )

        dry_out = StringIO()
        call_command("mark_suspect_issues", "--dry-run", "--issue-id", str(issue.pk), stdout=dry_out)
        issue.refresh_from_db()
        self.assertFalse(issue.review_required)
        self.assertIn("DRY-RUN", dry_out.getvalue())

        apply_out = StringIO()
        call_command("mark_suspect_issues", "--apply", "--issue-id", str(issue.pk), stdout=apply_out)
        issue.refresh_from_db()
        self.assertTrue(issue.review_required)
        self.assertTrue(any(reason.startswith("same_story_items=") for reason in issue.review_reasons))

    def test_issue_detail_separates_related_items_from_core_timeline(self):
        rumor_raw = self.make_raw_item(
            self.rumor_source,
            "Zelda remake leak reportedly planned",
            "https://rumor.example/zelda-remake-leak",
        )
        related_raw = self.make_raw_item(
            self.rumor_source,
            "Nintendo Switch 2 accessory rumor roundup",
            "https://rumor.example/switch-2-accessory-roundup",
        )
        core_item, _ = process_raw_item(rumor_raw)
        related_item, _ = process_raw_item(related_raw)
        issue = core_item.issue_links.get().issue
        related_item.issue_links.all().delete()
        NewsItemIssue.objects.create(
            news_item=related_item,
            issue=issue,
            relation=IssueRelation.RELATED,
            relation_confidence=0.2,
            explanation="manual related only",
        )

        response = self.client.get(reverse("news:issue_detail", args=[issue.pk]))
        html = response.content.decode()

        self.assertContains(response, "관련 항목")
        self.assertContains(response, "같은 이야기로 확정되지 않은 항목입니다")
        self.assertLess(html.index("Zelda remake leak reportedly planned"), html.index("관련 항목"))
        self.assertGreater(html.index("Nintendo Switch 2 accessory rumor roundup"), html.index("관련 항목"))

    def test_item_search_finds_matching_title(self):
        raw_item = self.make_raw_item(
            self.official_source,
            "Nintendo Direct showcase announced",
            "https://official.example/direct",
        )
        process_raw_item(raw_item)
        self.client.force_login(self.user)

        response = self.client.get(reverse("news:item_list"), {"q": "Direct showcase"})

        self.assertContains(response, "Nintendo Direct showcase announced")

    def test_read_pages_are_public_without_login(self):
        raw_item = self.make_raw_item(
            self.official_source,
            "Nintendo Direct showcase announced",
            "https://official.example/public-direct",
        )
        news_item, _created = process_raw_item(raw_item)
        issue = news_item.issue_links.select_related("issue").get().issue

        public_urls = [
            reverse("news:item_list"),
            reverse("news:item_detail", args=[news_item.pk]),
            reverse("news:issue_list"),
            reverse("news:issue_detail", args=[issue.pk]),
            reverse("news:source_list"),
            reverse("news:source_health"),
            reverse("news:franchise_list"),
            reverse("news:franchise_detail", args=[self.zelda.slug]),
        ]

        for url in public_urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)

        response = self.client.get(reverse("news:home"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("news:item_list"))

    def test_mutating_actions_still_require_login(self):
        raw_item = self.make_raw_item(
            self.official_source,
            "Nintendo Direct showcase announced",
            "https://official.example/protected-action",
        )
        news_item, _created = process_raw_item(raw_item)

        response = self.client.post(reverse("news:mark_read", args=[news_item.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/accounts/login/"))
        news_item.refresh_from_db()
        self.assertFalse(news_item.is_read)
