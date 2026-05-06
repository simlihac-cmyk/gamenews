from __future__ import annotations

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from news.models import Franchise, NewsItem, RawItem, Source, SourceType, TrustType, UserFranchiseFavorite
from news.services.collectors import process_raw_item
from news.services.text import create_content_hash


class PublicPageSecurityAndSeoTests(TestCase):
    def setUp(self) -> None:
        self.source = Source.objects.create(
            name="Nintendo UK News",
            slug="nintendo-uk",
            url="https://example.com/feed",
            source_type=SourceType.RSS,
            trust_type=TrustType.OFFICIAL,
        )
        self.zelda = Franchise.objects.create(name="Zelda", slug="zelda", aliases=["Zelda"], priority=90)
        self.user = get_user_model().objects.create_user(username="normal", password="password")
        self.staff = get_user_model().objects.create_user(username="staff", password="password", is_staff=True)

    def make_item(self, title: str = "Zelda release date announced", *, published_at=None) -> NewsItem:
        published_at = published_at or timezone.now()
        raw = RawItem.objects.create(
            source=self.source,
            title=title,
            url=f"https://example.com/news/{title.lower().replace(' ', '-')}",
            canonical_url=f"https://example.com/news/{title.lower().replace(' ', '-')}",
            published_at=published_at,
            raw_text="Nintendo announced a Zelda release date.",
            content_hash=create_content_hash(title, f"https://example.com/news/{title.lower().replace(' ', '-')}"),
        )
        item, _created = process_raw_item(raw)
        return item

    @override_settings(BACKUP_DIR="/app/backups/postgres")
    def test_public_status_page_hides_internal_backup_paths(self):
        response = self.client.get(reverse("news:source_health"))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn("/app/", html)
        self.assertNotIn("backups/postgres", html)
        self.assertNotIn(".sql.gz", html)

    def test_admin_nav_link_visibility(self):
        self.make_item()

        anonymous = self.client.get(reverse("news:item_list"))
        self.assertNotContains(anonymous, "관리자")
        self.assertNotContains(anonymous, "/admin/")

        self.client.force_login(self.user)
        normal = self.client.get(reverse("news:item_list"))
        self.assertNotContains(normal, "관리자")
        self.assertNotContains(normal, "/admin/")

        self.client.force_login(self.staff)
        staff = self.client.get(reverse("news:item_list"))
        self.assertContains(staff, "관리자")
        self.assertContains(staff, "/admin/")

    def test_favorites_filter_empty_states(self):
        self.make_item()

        anonymous = self.client.get(reverse("news:item_list"), {"favorites_only": "on"})
        self.assertContains(anonymous, "관심작을 보려면 로그인이 필요합니다.")

        self.client.force_login(self.user)
        no_favorites = self.client.get(reverse("news:item_list"), {"favorites_only": "on"})
        self.assertContains(no_favorites, "관심 프랜차이즈를 설정하면 맞춤 타임라인을 볼 수 있습니다.")

        UserFranchiseFavorite.objects.create(user=self.user, franchise=self.zelda)
        with_favorite = self.client.get(reverse("news:item_list"), {"favorites_only": "on"})
        self.assertContains(with_favorite, "Zelda release date announced")

    def test_item_list_sorting_uses_published_at_before_newly_detected_unknown_date(self):
        published = self.make_item("Zelda release date announced", published_at=timezone.now())
        unknown_raw = RawItem.objects.create(
            source=self.source,
            title="Kirby Air Riders article rediscovered",
            url="https://example.com/news/kirby-rediscovered",
            canonical_url="https://example.com/news/kirby-rediscovered",
            raw_text="An older article with no visible publication date.",
            content_hash=create_content_hash("Kirby Air Riders article rediscovered", "https://example.com/news/kirby-rediscovered"),
        )
        unknown, _created = process_raw_item(unknown_raw)

        response = self.client.get(reverse("news:item_list"))

        titles = [item.title for item in response.context["page"].object_list]
        self.assertLess(titles.index(published.title), titles.index(unknown.title))
        self.assertContains(response, "게시일 미상")

    def test_detected_sort_uses_first_seen(self):
        old = self.make_item("Zelda old published item", published_at=timezone.now() - timedelta(days=5))
        newest_raw = RawItem.objects.create(
            source=self.source,
            title="Kirby newly detected item",
            url="https://example.com/news/kirby-newly-detected",
            canonical_url="https://example.com/news/kirby-newly-detected",
            published_at=timezone.now() - timedelta(days=10),
            raw_text="Recently collected.",
            content_hash=create_content_hash("Kirby newly detected item", "https://example.com/news/kirby-newly-detected"),
        )
        newest, _created = process_raw_item(newest_raw)
        NewsItem.objects.filter(pk=old.pk).update(first_seen_at=timezone.now() - timedelta(days=2))
        NewsItem.objects.filter(pk=newest.pk).update(first_seen_at=timezone.now())

        response = self.client.get(reverse("news:item_list"), {"sort": "detected"})

        self.assertEqual(response.context["page"].object_list[0].pk, newest.pk)

    def test_canonical_noindex_jsonld_sitemap_and_robots(self):
        item = self.make_item()
        issue = item.issue_links.get().issue

        filtered = self.client.get(reverse("news:item_list"), {"favorites_only": "on"})
        self.assertContains(filtered, 'content="noindex,follow"')
        self.assertContains(filtered, f'href="http://testserver{reverse("news:item_list")}"')

        detail = self.client.get(reverse("news:item_detail", args=[item.pk]))
        self.assertContains(detail, f'href="http://testserver{reverse("news:item_detail", args=[item.pk])}"')
        self.assertContains(detail, 'type="application/ld+json"')
        json_ld = detail.content.decode().split('<script type="application/ld+json">', 1)[1].split("</script>", 1)[0]
        data = json.loads(json_ld)
        self.assertEqual(data["headline"], item.title)
        self.assertEqual(data["datePublished"], item.published_at.isoformat())

        issue_detail = self.client.get(reverse("news:issue_detail", args=[issue.pk]))
        self.assertContains(issue_detail, f'href="http://testserver{reverse("news:issue_detail", args=[issue.pk])}"')

        sitemap = self.client.get(reverse("news:sitemap_xml"))
        self.assertEqual(sitemap.status_code, 200)
        self.assertContains(sitemap, reverse("news:item_detail", args=[item.pk]))
        self.assertNotContains(sitemap, "favorites_only")

        robots = self.client.get(reverse("news:robots_txt"))
        self.assertContains(robots, "Sitemap:")
