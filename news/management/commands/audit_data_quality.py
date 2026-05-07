from __future__ import annotations

from django.core.management.base import BaseCommand

from news.models import Issue, NewsContentType, NewsItem, RawItem
from news.services.importance import reason_labels
from news.services.issues import issue_review_metrics, issue_review_reasons
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
            findings.append(
                _finding(
                    "date_problem",
                    "raw",
                    raw.pk,
                    raw.title,
                    "published_at=None,date_confidence=high",
                    "게시일 미상인데 날짜 신뢰도가 high입니다.",
                    "recalculate_scores 또는 raw date quality 재계산",
                )
            )

        for issue in Issue.objects.prefetch_related("news_links__news_item__franchise_links").order_by("-last_updated_at")[:500]:
            reasons = issue_review_reasons(issue)
            if reasons:
                metrics = issue_review_metrics(issue)
                findings.append(
                    _finding(
                        "suspect_issue",
                        "issue",
                        issue.pk,
                        issue.title,
                        f"items={metrics.item_count},sources={metrics.source_count},primary_game_types={metrics.primary_franchise_count},avg_similarity={metrics.average_title_similarity:.2f}",
                        ", ".join(reasons),
                        "mark_suspect_issues --apply 검토",
                    )
                )
            for link in issue.news_links.all():
                explanation = link.explanation or ""
                if any(marker in explanation for marker in ("same_story:", "score=", "shared_franchise", "decision=linked")):
                    findings.append(
                        _finding(
                            "debug_leakage_risk",
                            "issue_link",
                            link.pk,
                            issue.title,
                            explanation[:160],
                            "공개 HTML에 내부 클러스터링 설명이 노출될 위험이 있습니다.",
                            "템플릿 debug gating 확인",
                        )
                    )

        for line in findings:
            self.stdout.write(line)
        self.stdout.write(f"{len(findings)} finding(s); dry-run/read-only.")


def _item_findings(item: NewsItem) -> list[str]:
    findings: list[str] = []
    title = item.title or ""
    raw_title = item.raw_item.title if item.raw_item_id else title
    cleaned_raw = clean_title(raw_title, item.source.slug)
    if item.title_suspect or len(title) > 120 or "read more" in title.casefold() or has_body_start_pattern(title):
        findings.append(_finding("title_problem", "item", item.pk, title, f"title_suspect={item.title_suspect}", "제목 정제/품질 확인이 필요합니다.", "recalculate_scores --apply 검토"))
    elif cleaned_raw and cleaned_raw != title and ("read more" in raw_title.casefold() or len(raw_title) > 120):
        findings.append(_finding("title_cleanup", "item", item.pk, title, cleaned_raw, "원본 제목 정제 후보가 현재 제목과 다릅니다.", "recalculate_scores --apply 검토"))

    if item.published_at is None and item.date_confidence == "high":
        findings.append(_finding("date_problem", "item", item.pk, title, "published_at=None,date_confidence=high", "게시일 미상인데 날짜 신뢰도가 high입니다.", "recalculate_scores --apply"))
    if item.importance_score > 0 and not reason_labels(item.importance_reasons):
        findings.append(_finding("score_problem", "item", item.pk, title, f"importance_score={item.importance_score}", "중요도 reason이 비었거나 placeholder입니다.", "recalculate_scores --only-missing-reasons --apply"))
    if item.confidence_score > 0 and not reason_labels(item.trust_reasons):
        findings.append(_finding("score_problem", "item", item.pk, title, f"trust_score={item.confidence_score}", "신뢰도 reason이 비었거나 placeholder입니다.", "recalculate_scores --only-missing-reasons --apply"))
    if item.nintendo_relevance_score < 3 and item.importance_score >= 80:
        findings.append(_finding("relevance_problem", "item", item.pk, title, f"relevance={item.nintendo_relevance_score},importance={item.importance_score}", "닌텐도 관련성이 낮은데 중요도가 높습니다.", "recalculate_scores --apply"))
    if item.content_type in STATIC_CONTENT_TYPES and item.importance_score >= 80:
        findings.append(_finding("home_candidate_problem", "item", item.pk, title, f"content_type={item.content_type},importance={item.importance_score}", "정적/목록/허브가 홈 후보처럼 보입니다.", "recalculate_scores --apply"))
    if item.published_at is None and item.importance_score >= 80:
        findings.append(_finding("home_candidate_problem", "item", item.pk, title, f"published_at=None,importance={item.importance_score}", "게시일 미상인데 중요도가 높습니다.", "recalculate_scores --apply"))

    primary_count = item.franchise_links.filter(is_primary=True).count()
    if primary_count >= 5:
        findings.append(_finding("game_type_problem", "item", item.pk, title, f"primary_game_types={primary_count}", "주요 게임종류가 과도하게 많습니다.", "게임종류 재분류 또는 review_required 검토"))
    mention_slugs = [mention.get("slug") for mention in item.entity_mentions or [] if mention.get("slug")]
    if len(mention_slugs) != len(set(mention_slugs)):
        findings.append(_finding("game_type_problem", "item", item.pk, title, "duplicate entity_mentions", "언급된 게임종류가 중복 저장되어 있습니다.", "recalculate_scores 또는 재수집 검토"))

    metadata = item.raw_item.metadata or {}
    if item.source.trust_type == "official" and metadata.get("original_source") == "원출처 확인 필요":
        findings.append(_finding("source_problem", "item", item.pk, title, "official original_source_needed", "공식 출처에 원출처 확인 필요 문구가 붙었습니다.", "source attribution metadata 정리"))
    if item.source.trust_type == "press" and metadata.get("original_source") == "원출처 확인 필요":
        findings.append(_finding("source_problem", "item", item.pk, title, "press original_source_needed", "전문 매체 원출처 미추출은 행을 숨기는 편이 자연스럽습니다.", "source attribution metadata 정리"))
    return findings


def _finding(problem_type: str, object_type: str, object_id, title: str, current_value: str, reason: str, suggested_action: str) -> str:
    return (
        f"problem_type={problem_type} | object_type={object_type} | object_id={object_id} | "
        f"title={title} | current_value={current_value} | reason={reason} | suggested_action={suggested_action}"
    )
