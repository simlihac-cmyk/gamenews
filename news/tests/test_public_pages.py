from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from news.models import Franchise, Issue, IssueRelation, NewsContentType, NewsItem, NewsItemIssue, RawItem, Source, SourceType, TrustType, UserFranchiseFavorite
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
        self.assertContains(no_favorites, "관심 게임종류를 설정하면 맞춤 타임라인을 볼 수 있습니다.")

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

    def test_static_transparency_pages_and_footer_links(self):
        urls = [
            reverse("news:about"),
            reverse("news:methodology"),
            reverse("news:corrections"),
            reverse("news:privacy"),
            reverse("news:terms"),
        ]

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)

        response = self.client.get(reverse("news:item_list"))
        self.assertContains(response, reverse("news:about"))
        self.assertContains(response, reverse("news:privacy"))
        self.assertContains(response, reverse("news:terms"))

    def test_detail_uses_korean_summary_and_limits_public_excerpt(self):
        long_text = " ".join([f"Sentence {index} has Nintendo Switch 2 details." for index in range(40)])
        raw = RawItem.objects.create(
            source=self.source,
            title="Switch 2 rumor reportedly gains traction",
            url="https://example.com/news/switch-2-rumor",
            canonical_url="https://example.com/news/switch-2-rumor",
            published_at=timezone.now(),
            raw_text=long_text,
            content_hash=create_content_hash("Switch 2 rumor reportedly gains traction", "https://example.com/news/switch-2-rumor"),
        )
        item, _created = process_raw_item(raw)
        item.summary_ko = (
            "무슨 일?: 해외 출처의 Star Fox announced for Switch 2 항목입니다. "
            "왜 중요?: 신작 발표와 Switch 2 흐름에 직접 연결됩니다. "
            "확인 상태: 해외 매체 보도입니다. "
            "주의: 세부 맥락은 원문에서 확인하세요.\n"
            "핵심 내용:\n"
            "- 요약 섹션 제목만 있는 줄도 빈 칸 없이 보여야 합니다."
        )
        item.save(update_fields=["summary_ko"])

        response = self.client.get(reverse("news:item_detail", args=[item.pk]))
        html = response.content.decode()

        self.assertContains(response, 'class="card summary-card"')
        self.assertContains(response, 'class="summary-list"')
        self.assertContains(response, "무슨 일?")
        self.assertContains(response, "왜 중요?")
        self.assertContains(response, "확인 상태")
        self.assertContains(response, "주의")
        self.assertContains(response, "핵심 내용")
        self.assertGreaterEqual(html.count('class="summary-row"'), 4)
        self.assertContains(response, '<span class="summary-text">신작 발표와 Switch 2 흐름에 직접 연결됩니다.</span>')
        self.assertNotContains(response, '<span class="summary-text"></span>')
        self.assertContains(response, "원문 보기")
        excerpt = html.split('<div class="pre">', 1)[1].split("</div>", 1)[0]
        self.assertLessEqual(len(excerpt), 520)

    def test_list_deduplicates_labels_and_truncates_long_title(self):
        source = Source.objects.create(
            name="Reddit Rumor",
            slug="reddit-rumor",
            url="https://reddit.example/feed",
            source_type=SourceType.RSS,
            trust_type=TrustType.RUMOR,
        )
        title = "Switch 2 rumor leak " + ("very long title segment " * 5)
        raw = RawItem.objects.create(
            source=source,
            title=title,
            url="https://reddit.example/r/GamingLeaksAndRumours/comments/abc/switch-2-rumor",
            canonical_url="https://reddit.example/r/GamingLeaksAndRumours/comments/abc/switch-2-rumor",
            published_at=timezone.now(),
            raw_text="A Switch 2 rumor leak was posted.",
            content_hash=create_content_hash(title, "https://reddit.example/r/GamingLeaksAndRumours/comments/abc/switch-2-rumor"),
        )
        item, _created = process_raw_item(raw)

        response = self.client.get(reverse("news:item_list"), {"q": item.title[:20]})
        html = response.content.decode()

        self.assertNotIn("루머 루머", html)
        self.assertNotIn("트레일러 트레일러", html)
        self.assertNotIn("발매일 발매일", html)
        self.assertContains(response, item.title[:80])
        self.assertNotIn(f">{item.title}<", html)

        detail = self.client.get(reverse("news:item_detail", args=[item.pk]))
        self.assertContains(detail, item.title)

    def test_franchise_pages_render_each_item_once_and_use_common_badges(self):
        mario = Franchise.objects.create(name="Mario", slug="mario", aliases=["Mario", "Super Mario", "Mario Kart"], priority=90)
        pokemon = Franchise.objects.create(name="Pokémon", slug="pokemon", aliases=["Pokémon", "Pokemon"], priority=90)
        source = Source.objects.create(
            name="Reddit Rumor",
            slug="reddit-rumor-franchise",
            url="https://reddit.example/feed",
            source_type=SourceType.RSS,
            trust_type=TrustType.RUMOR,
        )
        raw = RawItem.objects.create(
            source=source,
            title="Mario Kart and Pokemon Switch 2 rumor",
            url="https://reddit.example/r/GamingLeaksAndRumours/comments/franchise/mario-pokemon",
            canonical_url="https://reddit.example/r/GamingLeaksAndRumours/comments/franchise/mario-pokemon",
            published_at=timezone.now(),
            raw_text="Mario Kart and Pokemon are both mentioned as part of a Switch 2 rumor.",
            content_hash=create_content_hash("Mario Kart and Pokemon Switch 2 rumor", "https://reddit.example/r/GamingLeaksAndRumours/comments/franchise/mario-pokemon"),
        )
        item, _created = process_raw_item(raw)
        item.entity_mentions = [
            {"slug": "mario", "name": "Mario", "matched_alias": "Mario", "is_primary": True},
            {"slug": "mario", "name": "Mario", "matched_alias": "Super Mario", "is_primary": True},
            {"slug": "pokemon", "name": "Pokémon", "matched_alias": "Pokemon", "is_primary": True},
        ]
        item.save(update_fields=["entity_mentions"])

        mario_response = self.client.get(reverse("news:franchise_detail", args=[mario.slug]))
        pokemon_response = self.client.get(reverse("news:franchise_detail", args=[pokemon.slug]))

        self.assertEqual([obj.pk for obj in mario_response.context["page"].object_list].count(item.pk), 1)
        self.assertEqual([obj.pk for obj in pokemon_response.context["page"].object_list].count(item.pk), 1)
        self.assertNotIn("루머 루머", mario_response.content.decode())

    def test_home_headlines_exclude_unknown_static_and_review_required_items(self):
        official = self.make_item("Switch 2 release date official reveal", published_at=timezone.now())
        unknown = self.make_item("Nintendo Switch 2 software list updated", published_at=timezone.now())
        NewsItem.objects.filter(pk=unknown.pk).update(
            published_at=None,
            importance_score=100,
            importance_reasons=["테스트"],
            nintendo_relevance_score=4,
        )
        static = self.make_item("Nintendo Switch 2 software list", published_at=timezone.now())
        NewsItem.objects.filter(pk=static.pk).update(
            content_type=NewsContentType.LIST_PAGE,
            importance_score=100,
            importance_reasons=["테스트"],
            nintendo_relevance_score=4,
        )
        review_item = self.make_item("Nintendo official review required item", published_at=timezone.now())
        issue = review_item.issue_links.get().issue
        issue.review_required = True
        issue.review_reasons = ["테스트"]
        issue.save(update_fields=["review_required", "review_reasons"])

        response = self.client.get(reverse("news:item_list"))
        headline_titles = [item.title for section in response.context["headline_sections"] for item in section["items"]]

        self.assertIn(official.title, headline_titles)
        self.assertNotIn(unknown.title, headline_titles)
        self.assertNotIn(static.title, headline_titles)
        self.assertNotIn(review_item.title, headline_titles)

    def test_home_headline_fallback_uses_recent_detected_candidates(self):
        recent = self.make_item("Switch 2 release date official reveal fallback", published_at=timezone.now() - timedelta(days=1))
        NewsItem.objects.filter(pk=recent.pk).update(importance_score=90, nintendo_relevance_score=4)

        response = self.client.get(reverse("news:item_list"))
        headline_titles = [item.title for section in response.context["headline_sections"] for item in section["items"]]

        self.assertIn(recent.title, headline_titles)

    def test_public_issue_pages_hide_clustering_debug_for_anonymous_and_show_for_staff(self):
        issue = Issue.objects.create(title="Debug issue", canonical_topic="debug issue")
        item = self.make_item("Debug issue same story", published_at=timezone.now())
        item.issue_links.all().delete()
        NewsItemIssue.objects.create(
            news_item=item,
            issue=issue,
            relation=IssueRelation.SAME_STORY,
            relation_confidence=0.9,
            explanation="decision=linked same_story: shared_franchise=True score=1.18",
            decision_debug={"decision": "linked", "score": 1.18},
        )

        with override_settings(DEBUG=False):
            anonymous = self.client.get(reverse("news:issue_detail", args=[issue.pk]))
        self.assertNotContains(anonymous, "same_story:")
        self.assertNotContains(anonymous, "score=1.18")
        self.assertContains(anonymous, "같은 이야기")

        self.client.force_login(self.staff)
        with override_settings(DEBUG=False):
            staff = self.client.get(reverse("news:issue_detail", args=[issue.pk]))
        self.assertContains(staff, "same_story:")
        self.assertContains(staff, "score=1.18")

    def test_source_attribution_rules_hide_unhelpful_original_source_placeholder(self):
        official = self.make_item("Switch 2 source attribution announced", published_at=timezone.now())
        press_source = Source.objects.create(
            name="Nintendo Life",
            slug="nintendo-life-attribution",
            url="https://example.com/feed",
            source_type=SourceType.RSS,
            trust_type=TrustType.PRESS,
        )
        press_raw = RawItem.objects.create(
            source=press_source,
            title="Nintendo Switch 2 report from media",
            url="https://example.com/news/media-report",
            canonical_url="https://example.com/news/media-report",
            published_at=timezone.now(),
            raw_text="Nintendo Switch 2 media report.",
            content_hash=create_content_hash("Nintendo Switch 2 report from media", "https://example.com/news/media-report"),
        )
        press_item, _created = process_raw_item(press_raw)
        rumor_source = Source.objects.create(
            name="GamingLeaksAndRumours Reddit RSS",
            slug="reddit-attribution",
            url="https://reddit.example/feed",
            source_type=SourceType.REDDIT_RSS,
            trust_type=TrustType.RUMOR,
        )
        rumor_raw = RawItem.objects.create(
            source=rumor_source,
            title="Switch 2 rumor without visible source",
            url="https://reddit.example/r/GamingLeaksAndRumours/comments/source/missing",
            canonical_url="https://reddit.example/r/GamingLeaksAndRumours/comments/source/missing",
            published_at=timezone.now(),
            raw_text="A Switch 2 rumor.",
            content_hash=create_content_hash("Switch 2 rumor without visible source", "https://reddit.example/r/GamingLeaksAndRumours/comments/source/missing"),
        )
        rumor_item, _created = process_raw_item(rumor_raw)

        official_html = self.client.get(reverse("news:item_detail", args=[official.pk])).content.decode()
        press_html = self.client.get(reverse("news:item_detail", args=[press_item.pk])).content.decode()
        rumor_html = self.client.get(reverse("news:item_detail", args=[rumor_item.pk])).content.decode()

        self.assertNotIn("원출처 확인 필요", official_html)
        self.assertNotIn("원출처 확인 필요", press_html)
        self.assertIn("원출처 확인 필요", rumor_html)

    def test_item_detail_hides_recalculation_placeholder_reasons(self):
        item = self.make_item("Switch 2 reason placeholder item", published_at=timezone.now())
        NewsItem.objects.filter(pk=item.pk).update(
            importance_reasons=["재계산 필요"],
            trust_reasons=["재계산 필요"],
        )

        response = self.client.get(reverse("news:item_detail", args=[item.pk]))

        self.assertNotContains(response, "재계산 필요")
        self.assertContains(response, "점수 설명 준비 중")

    def test_login_page_has_noindex_csrf_autocomplete_and_policy_links(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'content="noindex,nofollow"')
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, 'autocomplete="username"')
        self.assertContains(response, 'autocomplete="current-password"')
        self.assertContains(response, reverse("news:privacy"))
        self.assertContains(response, reverse("news:terms"))

    def test_source_pages_and_filter_group_sources(self):
        Source.objects.create(
            name="Nintendo Life",
            slug="nintendo-life",
            url="https://example.com/feed",
            source_type=SourceType.RSS,
            trust_type=TrustType.PRESS,
        )
        Source.objects.create(
            name="GamingLeaksAndRumours Reddit RSS",
            slug="gaming-leaks",
            url="https://example.com/rss",
            source_type=SourceType.REDDIT_RSS,
            trust_type=TrustType.RUMOR,
        )

        source_page = self.client.get(reverse("news:source_list"))
        self.assertContains(source_page, "공식 소스")
        self.assertContains(source_page, "전문 매체")
        self.assertContains(source_page, "루머 소스")
        self.assertContains(source_page, "공식 확인 전인 루머/유출성 출처입니다")

        item_page = self.client.get(reverse("news:item_list"))
        self.assertContains(item_page, '<optgroup label="공식 소스">', html=False)
        self.assertContains(item_page, '<optgroup label="전문 매체">', html=False)
        self.assertContains(item_page, '<optgroup label="루머 소스">', html=False)

    def test_game_type_label_and_seeded_names_are_korean(self):
        call_command("seed_sources", stdout=StringIO())

        response = self.client.get(reverse("news:item_list"))
        html = response.content.decode()

        self.assertContains(response, "게임종류")
        self.assertContains(response, "게임종류 전체")
        self.assertContains(response, "마리오")
        self.assertContains(response, "젤다의 전설")
        self.assertContains(response, "포켓몬")
        self.assertNotIn(">Mario<", html)
        self.assertNotIn(">Zelda<", html)
        self.assertNotIn(">Pokémon<", html)

    def test_active_filter_chips_and_filter_accessibility(self):
        self.make_item("Zelda release date announced")

        response = self.client.get(reverse("news:item_list"), {"q": "Zelda", "min_importance": "80"})

        self.assertContains(response, 'role="search"')
        self.assertContains(response, "<fieldset", html=False)
        self.assertContains(response, "활성 필터")
        self.assertContains(response, "검색: Zelda")
        self.assertContains(response, "중요도 80+")
        self.assertContains(response, "로그인하면 읽음, 북마크, 관심 게임종류를 저장할 수 있습니다.")
