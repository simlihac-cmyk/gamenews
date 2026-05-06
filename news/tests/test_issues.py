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
            "The Legend of Zelda release date announced",
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
        self.assertEqual(official_link.relation, IssueRelation.CONFIRMATION)

    def test_shared_franchise_can_group_low_token_overlap_confirmation(self):
        rumor_raw = self.make_raw_item(
            self.rumor_source,
            "Zelda remake leak",
            "https://rumor.example/zelda-remake",
        )
        official_raw = self.make_raw_item(
            self.official_source,
            "The Legend of Zelda launches next year",
            "https://official.example/zelda-launch",
            raw_text="An official release date window was announced.",
        )

        process_raw_item(rumor_raw)
        process_raw_item(official_raw)

        issue = Issue.objects.get()
        self.assertEqual(Issue.objects.count(), 1)
        self.assertEqual(issue.status, IssueStatus.CONFIRMED)

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
