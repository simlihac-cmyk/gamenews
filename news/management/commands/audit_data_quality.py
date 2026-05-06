from __future__ import annotations

from django.core.management.base import BaseCommand

from news.models import Issue, NewsContentType, NewsItem, RawItem
from news.services.issues import issue_review_reasons
from news.services.quality import clean_title, has_body_start_pattern


STATIC_CONTENT_TYPES = {
    NewsContentType.STATIC_PAGE,
    NewsContentType.LIST_PAGE,
    NewsContentType.HUB_PAGE,
}


class Command(BaseCommand):
    help = "Audit data quality issues across items, issues, dates, scores, and source attribution. Read-only."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Accepted for safety; this command is read-only.")
        parser.add_argument("--limit", type=int, default=None, help="Maximum NewsItem rows to scan.")

    def handle(self, *args, **options):
        findings: list[str] = []
        item_qs = NewsItem.objects.select_related("raw_item", "source").prefetch_related("franchise_links__franchise", "issue_links__issue").order_by("-created_at")
        if options["limit"]:
            item_qs = item_qs[: options["limit"]]

        for item in item_qs:
            findings.extend(_item_findings(item))

        for raw in RawItem.objects.select_related("source").filter(published_at__isnull=True, date_confidence="high")[:100]:
            findings.append(f"date_problem raw #{raw.pk}: published_at=None but date_confidence=high | {raw.title}")

        for issue in Issue.objects.prefetch_related("news_links__news_item__franchise_links").order_by("-last_updated_at")[:500]:
            reasons = issue_review_reasons(issue)
            if reasons:
                findings.append(f"suspect_issue issue #{issue.pk}: {', '.join(reasons)} | {issue.title}")

        for line in findings:
            self.stdout.write(line)
        self.stdout.write(f"{len(findings)} finding(s); dry-run/read-only.")


def _item_findings(item: NewsItem) -> list[str]:
    findings: list[str] = []
    title = item.title or ""
    raw_title = item.raw_item.title if item.raw_item_id else title
    cleaned_raw = clean_title(raw_title, item.source.slug)
    if item.title_suspect or len(title) > 120 or "read more" in title.casefold() or has_body_start_pattern(title):
        findings.append(f"title_problem item #{item.pk}: title_suspect={item.title_suspect} | {title}")
    elif cleaned_raw and cleaned_raw != title and ("read more" in raw_title.casefold() or len(raw_title) > 120):
        findings.append(f"title_cleanup item #{item.pk}: cleaned candidate differs | {title}")

    if item.published_at is None and item.date_confidence == "high":
        findings.append(f"date_problem item #{item.pk}: published_at=None but date_confidence=high | {title}")
    if item.importance_score > 0 and not item.importance_reasons:
        findings.append(f"score_problem item #{item.pk}: importance reasons missing | {title}")
    if item.confidence_score > 0 and not item.trust_reasons:
        findings.append(f"score_problem item #{item.pk}: trust reasons missing | {title}")
    if item.nintendo_relevance_score < 3 and item.importance_score >= 80:
        findings.append(f"relevance_problem item #{item.pk}: relevance={item.nintendo_relevance_score}, importance={item.importance_score} | {title}")
    if item.content_type in STATIC_CONTENT_TYPES and item.importance_score >= 80:
        findings.append(f"home_candidate_problem item #{item.pk}: static/list/hub high importance | {title}")
    if item.published_at is None and item.importance_score >= 80:
        findings.append(f"home_candidate_problem item #{item.pk}: unknown date high importance | {title}")

    primary_count = item.franchise_links.filter(is_primary=True).count()
    if primary_count >= 5:
        findings.append(f"game_type_problem item #{item.pk}: primary game types={primary_count} | {title}")
    mention_slugs = [mention.get("slug") for mention in item.entity_mentions or [] if mention.get("slug")]
    if len(mention_slugs) != len(set(mention_slugs)):
        findings.append(f"game_type_problem item #{item.pk}: duplicate entity_mentions | {title}")

    metadata = item.raw_item.metadata or {}
    if item.source.trust_type == "official" and metadata.get("original_source") == "원출처 확인 필요":
        findings.append(f"source_problem item #{item.pk}: official item has original-source-needed marker | {title}")
    if item.source.trust_type == "press" and metadata.get("original_source") == "원출처 확인 필요":
        findings.append(f"source_problem item #{item.pk}: press item forces original-source-needed marker | {title}")
    return findings
