from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from django.db.models import Q
from django.utils import timezone

from news.models import Issue, IssueRelation, NewsContentType, NewsItem, NewsItemIssue, TrustLabel

from .quality import clean_title, has_body_start_pattern, series_marker_for
from .text import canonical_topic_from_title, normalize_title


STATIC_CONTENT_TYPES = {
    NewsContentType.STATIC_PAGE,
    NewsContentType.LIST_PAGE,
    NewsContentType.HUB_PAGE,
}
SUSPECT_ISSUE_TITLE = "검토 필요: 여러 주제 혼합 가능성"
TITLE_DATE_PREFIX_RE = re.compile(r"^\s*(?:\d{1,2}/\d{1,2}/\d{2,4}|\d{4}[.-]\d{1,2}[.-]\d{1,2})\b")


@dataclass(frozen=True)
class IssueReviewMetrics:
    item_count: int
    same_story_count: int
    source_count: int
    primary_franchise_count: int
    average_title_similarity: float
    content_type_count: int
    series_marker_count: int
    weak_only_count: int
    duplicate_only: bool


def issue_review_reasons(issue: Issue) -> list[str]:
    links = _review_links(issue)
    same_story_links = _same_story_links(links)
    reasons: list[str] = []
    same_story_count = len(same_story_links)
    canonical_urls = {
        link.news_item.canonical_url
        for link in same_story_links
        if link.news_item.canonical_url
    }
    duplicate_only = bool(canonical_urls) and len(canonical_urls) == 1
    if same_story_count >= 10 and not duplicate_only:
        reasons.append(f"same_story_items={same_story_count}")

    source_ids = {link.news_item.source_id for link in same_story_links if link.news_item.source_id}

    primary_franchise_ids = {
        franchise_link.franchise_id
        for link in same_story_links
        for franchise_link in link.news_item.franchise_links.all()
        if franchise_link.is_primary
    }
    if len(primary_franchise_ids) >= 4:
        reasons.append(f"primary_franchises={len(primary_franchise_ids)}")

    average_similarity = _average_title_similarity([link.news_item.title for link in same_story_links])
    if same_story_count >= 4 and average_similarity < 0.35 and not duplicate_only:
        reasons.append(f"low_title_similarity={average_similarity:.2f}")
    if same_story_count >= 4 and len(source_ids) == 1 and average_similarity < 0.45 and not duplicate_only:
        reasons.append(f"single_source_title_diversity={average_similarity:.2f}")

    weak_only = [
        link
        for link in same_story_links
        if link.decision_debug
        and not link.decision_debug.get("strong_signals")
        and link.decision_debug.get("weak_signals")
    ]
    if weak_only:
        reasons.append(f"weak_only_links={len(weak_only)}")

    content_types = {link.news_item.content_type for link in same_story_links if link.news_item.content_type}
    if same_story_count >= 4 and len(content_types) >= 3 and not duplicate_only:
        reasons.append(f"mixed_content_types={len(content_types)}")

    series_markers = {
        marker
        for link in same_story_links
        if (
            marker := series_marker_for(
                link.news_item.title,
                getattr(link.news_item.raw_item, "raw_text", ""),
                link.news_item.url,
            )
        )
    }
    if same_story_count >= 3 and series_markers and (average_similarity < 0.65 or len(primary_franchise_ids) >= 2):
        reasons.append(f"mixed_official_series={','.join(sorted(series_markers))}")

    title = issue.title or ""
    if len(title) > 120:
        reasons.append("long_issue_title")
    if "read more" in title.casefold():
        reasons.append("read_more_in_issue_title")
    if has_body_start_pattern(title):
        reasons.append("body_start_in_issue_title")
    if TITLE_DATE_PREFIX_RE.search(title) and has_body_start_pattern(title):
        reasons.append("date_prefix_with_body_start")
    return _dedupe(reasons)


def issue_review_metrics(issue: Issue) -> IssueReviewMetrics:
    links = _review_links(issue)
    same_story_links = _same_story_links(links)
    canonical_urls = {
        link.news_item.canonical_url
        for link in same_story_links
        if link.news_item.canonical_url
    }
    primary_franchise_ids = {
        franchise_link.franchise_id
        for link in same_story_links
        for franchise_link in link.news_item.franchise_links.all()
        if franchise_link.is_primary
    }
    weak_only = [
        link
        for link in same_story_links
        if link.decision_debug
        and not link.decision_debug.get("strong_signals")
        and link.decision_debug.get("weak_signals")
    ]
    content_types = {link.news_item.content_type for link in same_story_links if link.news_item.content_type}
    series_markers = {
        marker
        for link in same_story_links
        if (
            marker := series_marker_for(
                link.news_item.title,
                getattr(link.news_item.raw_item, "raw_text", ""),
                link.news_item.url,
            )
        )
    }
    return IssueReviewMetrics(
        item_count=len(links),
        same_story_count=len(same_story_links),
        source_count=len({link.news_item.source_id for link in same_story_links if link.news_item.source_id}),
        primary_franchise_count=len(primary_franchise_ids),
        average_title_similarity=_average_title_similarity([link.news_item.title for link in same_story_links]),
        content_type_count=len(content_types),
        series_marker_count=len(series_markers),
        weak_only_count=len(weak_only),
        duplicate_only=bool(canonical_urls) and len(canonical_urls) == 1,
    )


def refresh_issue_review_status(issue: Issue, *, save: bool = True) -> tuple[bool, list[str]]:
    reasons = issue_review_reasons(issue)
    review_required = bool(reasons)
    if save and (issue.review_required != review_required or issue.review_reasons != reasons):
        issue.review_required = review_required
        issue.review_reasons = reasons
        issue.save(update_fields=["review_required", "review_reasons", "updated_at"])
    return review_required, reasons


def select_issue_title(issue: Issue) -> str:
    candidates = list(
        NewsItem.objects.filter(issue_links__issue=issue)
        .select_related("source")
        .filter(is_archived=False, raw_item__rejection_reason="")
        .filter(Q(published_at__isnull=True) | Q(published_at__lte=timezone.now()))
        .order_by("-importance_score", "-confidence_score", "first_seen_at")
        .distinct()
    )
    usable = [item for item in candidates if _is_usable_issue_title_item(item)]
    if not usable:
        cleaned = clean_title(issue.title)
        if cleaned and len(cleaned) <= 120 and "read more" not in cleaned.casefold() and not has_body_start_pattern(cleaned):
            return cleaned
        return SUSPECT_ISSUE_TITLE

    if issue.review_required and _issue_title_polluted(issue.title) and issue_review_metrics(issue).primary_franchise_count >= 4:
        return SUSPECT_ISSUE_TITLE

    official = [item for item in usable if item.trust_label == TrustLabel.OFFICIAL]
    selected = (official or usable)[0]
    return selected.title


def rebuild_issue_title(issue: Issue, *, save: bool = True) -> tuple[str, bool]:
    title = select_issue_title(issue)
    changed = issue.title != title
    if save and changed:
        issue.title = title
        issue.canonical_topic = canonical_topic_from_title(title)
        issue.save(update_fields=["title", "canonical_topic", "updated_at"])
    return title, changed


def _is_usable_issue_title_item(item: NewsItem) -> bool:
    title = item.title or ""
    return not (
        item.title_suspect
        or len(title) > 120
        or "read more" in title.casefold()
        or has_body_start_pattern(title)
        or item.content_type in STATIC_CONTENT_TYPES
    )


def _issue_title_polluted(title: str) -> bool:
    value = title or ""
    return bool(
        len(value) > 120
        or "read more" in value.casefold()
        or has_body_start_pattern(value)
        or (TITLE_DATE_PREFIX_RE.search(value) and has_body_start_pattern(value))
    )


def _review_links(issue: Issue) -> list[NewsItemIssue]:
    return list(
        issue.news_links.select_related("news_item", "news_item__raw_item", "news_item__source")
        .prefetch_related("news_item__franchise_links")
        .filter(
            news_item__is_archived=False,
            news_item__raw_item__rejection_reason="",
        )
    )


def _same_story_links(links: list[NewsItemIssue]) -> list[NewsItemIssue]:
    return [
        link
        for link in links
        if link.relation in {IssueRelation.SAME_STORY, IssueRelation.SOURCE_DUPLICATE}
    ]


def _average_title_similarity(titles: list[str]) -> float:
    normalized = [normalize_title(title) for title in titles if normalize_title(title)]
    if len(normalized) < 2:
        return 1.0
    total = 0.0
    comparisons = 0
    for index, left in enumerate(normalized):
        for right in normalized[index + 1 :]:
            total += difflib.SequenceMatcher(None, left, right).ratio()
            comparisons += 1
    return total / comparisons if comparisons else 1.0


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
