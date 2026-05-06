from __future__ import annotations

from django.test import TestCase

from news.models import (
    Franchise,
    Issue,
    IssueRelation,
    IssueStatus,
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
