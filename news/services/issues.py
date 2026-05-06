from __future__ import annotations

import difflib

from django.db.models import Q
from django.utils import timezone

from news.models import Issue, IssueRelation, NewsContentType, NewsItem, NewsItemIssue, TrustLabel

from .quality import clean_title, has_body_start_pattern
from .text import canonical_topic_from_title, normalize_title


STATIC_CONTENT_TYPES = {
    NewsContentType.STATIC_PAGE,
    NewsContentType.LIST_PAGE,
    NewsContentType.HUB_PAGE,
}
SUSPECT_ISSUE_TITLE = "검토 필요: 여러 주제 혼합 가능성"


def issue_review_reasons(issue: Issue) -> list[str]:
    links = list(
        issue.news_links.select_related("news_item")
        .prefetch_related("news_item__franchise_links")
        .filter(
            news_item__is_archived=False,
            news_item__raw_item__rejection_reason="",
        )
    )
    same_story_links = [
        link
        for link in links
        if link.relation in {IssueRelation.SAME_STORY, IssueRelation.SOURCE_DUPLICATE}
    ]
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

    weak_only = [
        link
        for link in same_story_links
        if link.decision_debug
        and not link.decision_debug.get("strong_signals")
        and link.decision_debug.get("weak_signals")
    ]
    if weak_only:
        reasons.append(f"weak_only_links={len(weak_only)}")

    title = issue.title or ""
    if len(title) > 120:
        reasons.append("long_issue_title")
    if "read more" in title.casefold():
        reasons.append("read_more_in_issue_title")
    if has_body_start_pattern(title):
        reasons.append("body_start_in_issue_title")
    return _dedupe(reasons)


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
        return cleaned if cleaned and len(cleaned) <= 120 and "read more" not in cleaned.casefold() else SUSPECT_ISSUE_TITLE

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
