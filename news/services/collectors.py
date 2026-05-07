from __future__ import annotations

import calendar
import difflib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import timedelta, timezone as datetime_timezone
from typing import Any
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import feedparser
import httpx
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from news.models import (
    Issue,
    IssueRelation,
    IssueStatus,
    NewsCategory,
    NewsContentType,
    NewsItem,
    NewsItemFranchise,
    NewsItemIssue,
    PublishedAtPrecision,
    RawItem,
    Source,
    SourceType,
    TrustLabel,
)

from .classifier import classify_item, detect_franchise_matches
from .dedupe import content_hash_for, find_existing_news_item, find_existing_raw_item
from .importance import calculate_importance_with_reasons, calculate_nintendo_relevance, score_reason
from .issues import rebuild_issue_title, refresh_issue_review_status
from .quality import (
    LOW_CONFIDENCE_THRESHOLD,
    article_rejection_reason,
    classify_content_type,
    date_quality_update_fields,
    extraction_confidence_for,
    is_backfill_item,
    precision_for_datetime,
    summary_quality_fallback,
    title_quality,
)
from .source_adapters import adapter_config
from .summarizer import summarize_item
from .text import (
    canonical_topic_from_title,
    normalize_title,
    normalize_url,
    normalize_whitespace,
    create_url_hash,
    strip_html,
    tokenize_topic,
    truncate,
)

logger = logging.getLogger(__name__)

PROTECTED_HEADER_NAMES = {"authorization", "cookie", "proxy-authorization"}
DEFAULT_EXCLUDE_PATTERNS = [
    "/privacy",
    "/terms",
    "/support",
    "/contact",
    "/login",
    "/account",
    "/feed",
    "/rss",
    "#",
]
DEFAULT_TITLE_EXCLUDE_EXACT = {
    "news rss",
    "features rss",
    "reviews rss",
    "rss feed",
    "subscribe to our rss feed",
}
RELATED_CATEGORY_GROUPS = [
    {NewsCategory.RUMOR, NewsCategory.LEAK},
    {
        NewsCategory.OFFICIAL,
        NewsCategory.DIRECT,
        NewsCategory.RELEASE_DATE,
        NewsCategory.TRAILER,
        NewsCategory.NEW_GAME,
        NewsCategory.UPDATE,
        NewsCategory.GENERAL,
    },
]
BROAD_ISSUE_TOKENS = {
    "nintendo",
    "switch",
    "switch2",
    "mario",
    "pokemon",
    "pokémon",
    "zelda",
    "official",
    "confirmed",
    "direct",
    "eshop",
    "news",
    "update",
    "updates",
    "trailer",
    "release",
    "released",
    "sale",
    "sales",
    "game",
    "games",
    "닌텐도",
    "스위치",
    "마리오",
    "포켓몬",
    "젤다",
    "공식",
    "뉴스",
}


@dataclass(frozen=True)
class IssueMatch:
    issue: Issue
    relation: str
    confidence: float
    explanation: str
    decision_debug: dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectResult:
    source: Source
    raw_items: list[RawItem] = field(default_factory=list)
    found_count: int = 0
    created_count: int = 0
    duplicate_count: int = 0
    skipped_count: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run_items: list[dict[str, Any]] = field(default_factory=list)
    requested_url: str = ""
    elapsed_seconds: float = 0.0


def fetch_enabled_sources():
    return Source.objects.filter(enabled=True).order_by("name")


def _fetch_url(source: Source, url: str) -> httpx.Response:
    config = adapter_config(source)
    timeout = _bounded_float(config.get("timeout_seconds"), settings.COLLECTOR_TIMEOUT_SECONDS, minimum=1.0, maximum=30.0)
    retries = int(_bounded_float(config.get("retries"), 1, minimum=0, maximum=2))
    headers = _request_headers(source)
    max_bytes = int(_bounded_float(config.get("max_response_bytes"), 5_000_000, minimum=50_000, maximum=20_000_000))

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        started = time.monotonic()
        try:
            logger.debug("HTTP GET source=%s url=%s attempt=%s", source.slug, url, attempt + 1)
            response = httpx.get(
                url,
                headers=headers,
                follow_redirects=True,
                timeout=timeout,
            )
            elapsed = time.monotonic() - started
            logger.info(
                "HTTP response source=%s status=%s bytes=%s elapsed=%.2fs url=%s",
                source.slug,
                response.status_code,
                len(response.content),
                elapsed,
                str(response.url),
            )
            if len(response.content) > max_bytes:
                raise ValueError(f"Response too large: {len(response.content)} bytes > {max_bytes}")
            if response.status_code == 429:
                raise httpx.HTTPStatusError("Rate limited by remote server", request=response.request, response=response)
            if 500 <= response.status_code < 600 and attempt < retries:
                time.sleep(min(0.5 * (attempt + 1), 2.0))
                continue
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < retries:
                logger.warning("Retrying source=%s after HTTP error: %s", source.slug, exc)
                time.sleep(min(0.5 * (attempt + 1), 2.0))
                continue
            raise

    if last_exc:
        raise last_exc
    raise RuntimeError(f"Failed to fetch URL: {url}")


def _request_headers(source: Source) -> dict[str, str]:
    headers = {
        "User-Agent": settings.NINTENDO_WATCH_USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, text/html;q=0.9, */*;q=0.8",
        "Accept-Language": "ko,en;q=0.8,ja;q=0.7",
    }
    for name, value in adapter_config(source).get("http_headers", {}).items():
        if name.lower() in PROTECTED_HEADER_NAMES:
            logger.warning("Ignoring protected HTTP header in Source.config source=%s header=%s", source.slug, name)
            continue
        headers[str(name)] = str(value)
    return headers


def _bounded_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    return max(minimum, min(maximum, number))


def collect_source(source: Source, *, limit: int | None = None, dry_run: bool = False) -> CollectResult:
    started = time.monotonic()
    checked_at = timezone.now()
    logger.info(
        "Starting fetch for source=%s type=%s dry_run=%s limit=%s",
        source.slug,
        source.source_type,
        dry_run,
        limit,
    )
    source.last_checked_at = checked_at
    source.last_error = ""
    source.last_new_items_count = 0
    source.save(update_fields=["last_checked_at", "last_error", "last_new_items_count", "updated_at"])

    try:
        if source.source_type == SourceType.RSS or source.source_type == SourceType.GOOGLE_ALERT_RSS:
            result = collect_rss(source, limit=limit, dry_run=dry_run)
        elif source.source_type == SourceType.YOUTUBE_RSS:
            result = collect_youtube_rss(source, limit=limit, dry_run=dry_run)
        elif source.source_type == SourceType.REDDIT_RSS:
            result = collect_reddit_rss(source, limit=limit, dry_run=dry_run)
        elif source.source_type == SourceType.HTML:
            result = collect_html(source, limit=limit, dry_run=dry_run)
        else:
            raise ValueError(f"Unsupported source type: {source.source_type}")
    except Exception as exc:  # noqa: BLE001 - one broken source should not stop the fetch command.
        elapsed = time.monotonic() - started
        error = _error_text(exc)
        logger.exception("Collection failed for source=%s elapsed=%.2fs error=%s", source.slug, elapsed, error)
        source.last_error = error
        source.last_new_items_count = 0
        source.last_fetch_duration_seconds = elapsed
        source.save(update_fields=["last_error", "last_new_items_count", "last_fetch_duration_seconds", "updated_at"])
        return CollectResult(source=source, errors=[error], elapsed_seconds=elapsed)

    result.elapsed_seconds = time.monotonic() - started
    source.last_success_at = timezone.now()
    if result.errors:
        source.last_error = "; ".join(result.errors)[:3000]
    else:
        source.last_error = ""
    source.last_new_items_count = result.created_count
    source.last_fetch_duration_seconds = result.elapsed_seconds
    if source.average_fetch_duration_seconds is None:
        source.average_fetch_duration_seconds = result.elapsed_seconds
    else:
        source.average_fetch_duration_seconds = round((source.average_fetch_duration_seconds * 0.8) + (result.elapsed_seconds * 0.2), 3)
    source.save(
        update_fields=[
            "last_success_at",
            "last_error",
            "last_new_items_count",
            "last_fetch_duration_seconds",
            "average_fetch_duration_seconds",
            "updated_at",
        ]
    )
    logger.info(
        "Finished fetch for source=%s url=%s found=%s created=%s duplicates=%s skipped=%s errors=%s elapsed=%.2fs",
        source.slug,
        result.requested_url or source.url,
        result.found_count,
        result.created_count,
        result.duplicate_count,
        result.skipped_count,
        len(result.errors),
        result.elapsed_seconds,
    )
    return result


def collect_rss(source: Source, *, limit: int | None = None, dry_run: bool = False, feed_url: str | None = None) -> CollectResult:
    url = feed_url or source.url
    if not url:
        raise ValueError("RSS source URL is empty")

    response = _fetch_url(source, url)

    parsed = feedparser.parse(response.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"Could not parse feed: {parsed.bozo_exception}")
    if parsed.bozo:
        logger.warning("Feed parsed with warnings for source=%s warning=%s", source.slug, parsed.bozo_exception)

    result = CollectResult(source=source, requested_url=url)
    entries = list(parsed.entries)
    entries = entries[:limit] if limit else entries
    if not entries:
        result.errors.append("No RSS entries found in feed.")
    for entry in entries:
        try:
            payload = _payload_from_feed_entry(source, entry)
            _store_payload(result, payload, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            error = _error_text(exc)
            logger.warning("Failed to store RSS entry from source=%s error=%s", source.slug, error)
            result.errors.append(error)
    return result


def collect_youtube_rss(source: Source, *, limit: int | None = None, dry_run: bool = False) -> CollectResult:
    config = adapter_config(source)
    channel_id = (config.get("channel_id") or "").strip()
    feed_url = source.url or config.get("feed_url") or (
        f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}" if channel_id else ""
    )
    if not feed_url:
        raise ValueError("YouTube RSS source needs url or config.channel_id")
    return collect_rss(source, limit=limit, dry_run=dry_run, feed_url=feed_url)


def collect_reddit_rss(source: Source, *, limit: int | None = None, dry_run: bool = False) -> CollectResult:
    return collect_rss(source, limit=limit, dry_run=dry_run)


def collect_html(source: Source, *, limit: int | None = None, dry_run: bool = False) -> CollectResult:
    if not source.url:
        raise ValueError("HTML source URL is empty")
    response = _fetch_url(source, source.url)
    config = adapter_config(source)
    soup = BeautifulSoup(response.text, config.get("parser", "html.parser"))
    result = CollectResult(source=source, requested_url=source.url)

    if config.get("item_selector"):
        payloads = _html_payloads_from_selectors(source, soup, response.text)
    elif config.get("embedded_json_selector"):
        payloads = _html_payloads_from_embedded_json(source, soup, response.text)
    else:
        payloads = _html_payloads_generic(source, soup, response.text)

    if limit:
        payloads = payloads[:limit]

    for payload in payloads:
        try:
            _store_payload(result, payload, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            error = _error_text(exc)
            logger.warning("Failed to store HTML entry from source=%s error=%s", source.slug, error)
            result.errors.append(error)
    if not payloads:
        message = "No candidate HTML items found; check Source.config selectors or URL patterns."
        logger.warning("HTML source produced no payloads source=%s", source.slug)
        result.errors.append(message)
    return result


def process_raw_item(raw_item: RawItem, *, recalculate: bool = False) -> tuple[NewsItem | None, bool]:
    title_info = title_quality(raw_item.title, raw_item.source.slug)
    display_title = title_info.cleaned or raw_item.title
    franchise_matches = detect_franchise_matches(display_title, raw_item.raw_text)
    primary_franchise_matches = [match for match in franchise_matches if match.is_primary]
    franchises = [match.franchise for match in primary_franchise_matches]
    date_quality = date_quality_update_fields(raw_item.published_at)
    content_type = classify_content_type(
        title=display_title,
        url=raw_item.canonical_url or raw_item.url,
        raw_text=raw_item.raw_text,
        source=raw_item.source,
    )
    confidence = extraction_confidence_for(
        title=raw_item.title,
        url=raw_item.canonical_url or raw_item.url,
        raw_text=raw_item.raw_text,
        published_at=raw_item.published_at,
        source=raw_item.source,
        franchise_count=len(franchises),
    )
    rejection_reason = article_rejection_reason(
        title=raw_item.title,
        url=raw_item.canonical_url or raw_item.url,
        raw_text=raw_item.raw_text,
        published_at=raw_item.published_at,
        source=raw_item.source,
    )
    if not rejection_reason and confidence < LOW_CONFIDENCE_THRESHOLD:
        rejection_reason = "low_extraction_confidence"
    updates: list[str] = []
    for field, value in date_quality.items():
        if getattr(raw_item, field) != value:
            setattr(raw_item, field, value)
            updates.append(field)
    if date_quality["is_date_suspect"] and not rejection_reason:
        rejection_reason = "date_suspect"
    if raw_item.rejection_reason != rejection_reason:
        raw_item.rejection_reason = rejection_reason
        updates.append("rejection_reason")
    if raw_item.extraction_confidence != confidence:
        raw_item.extraction_confidence = confidence
        updates.append("extraction_confidence")
    if raw_item.published_at and raw_item.published_at_precision == PublishedAtPrecision.UNKNOWN:
        raw_item.published_at_precision = PublishedAtPrecision.EXACT
        updates.append("published_at_precision")
    if updates:
        raw_item.save(update_fields=updates)

    if rejection_reason:
        if hasattr(raw_item, "news_item"):
            raw_item.news_item.is_archived = True
            raw_item.news_item.extraction_confidence = confidence
            raw_item.news_item.date_confidence = date_quality["date_confidence"]
            raw_item.news_item.is_date_suspect = bool(date_quality["is_date_suspect"])
            raw_item.news_item.date_suspect_reason = str(date_quality["date_suspect_reason"])
            raw_item.news_item.save(
                update_fields=[
                    "is_archived",
                    "extraction_confidence",
                    "date_confidence",
                    "is_date_suspect",
                    "date_suspect_reason",
                    "updated_at",
                ]
            )
            return raw_item.news_item, False
        logger.info("Rejected raw item id=%s source=%s reason=%s title=%s", raw_item.pk, raw_item.source.slug, rejection_reason, raw_item.title)
        return None, False

    existing = find_existing_news_item(
        raw_item=raw_item,
        canonical_url=raw_item.canonical_url or "",
        title=display_title,
    )
    if existing and not recalculate:
        return existing, False

    classification = classify_item(raw_item.source, display_title, raw_item.raw_text)
    is_backfill = is_backfill_item(raw_item.published_at, raw_item.first_seen_at)
    relevance = calculate_nintendo_relevance(
        source=raw_item.source,
        title=display_title,
        raw_text=raw_item.raw_text,
        tags=classification.tags,
        franchises=franchises,
    )
    importance, importance_reasons = calculate_importance_with_reasons(
        source=raw_item.source,
        title=display_title,
        raw_text=raw_item.raw_text,
        tags=classification.tags,
        franchises=franchises,
        published_at=raw_item.published_at,
        first_seen_at=raw_item.first_seen_at,
        content_type=content_type,
        title_suspect=title_info.is_suspect,
        date_confidence=str(date_quality["date_confidence"]),
        nintendo_relevance_score=relevance,
    )
    if importance > 0 and not importance_reasons:
        importance_reasons = [score_reason("fallback_importance_reason", "자동 중요도 계산 기준")]
    if classification.confidence_score > 0 and not classification.trust_reasons:
        classification.trust_reasons.append(score_reason("fallback_trust_reason", "출처 기준 신뢰도 계산"))
    summary_ko = summary_quality_fallback(
        trust_label=classification.trust_label,
        content_type=content_type,
        title_suspect=title_info.is_suspect,
    ) or summarize_item(
        source=raw_item.source,
        title=display_title,
        raw_text=raw_item.raw_text,
        classification=classification,
    )
    thumbnail_url = raw_item.metadata.get("thumbnail_url", "") if raw_item.metadata else ""

    fields = {
        "source": raw_item.source,
        "title": display_title,
        "normalized_title": normalize_title(display_title),
        "url": raw_item.url,
        "canonical_url": raw_item.canonical_url,
        "canonical_url_hash": raw_item.canonical_url_hash or create_url_hash(raw_item.canonical_url or ""),
        "summary_ko": summary_ko,
        "summary_original": truncate(raw_item.raw_text, 1500),
        "title_suspect": title_info.is_suspect,
        "title_suspect_reason": title_info.reason,
        "content_type": content_type,
        "nintendo_relevance_score": relevance,
        "trust_label": classification.trust_label,
        "category": classification.category,
        "detected_tags": classification.tags,
        "confidence_score": classification.confidence_score,
        "importance_score": importance,
        "importance_reasons": importance_reasons,
        "trust_reasons": classification.trust_reasons,
        "entity_mentions": _entity_mentions_payload(franchise_matches),
        "region": raw_item.source.region,
        "language": raw_item.source.language,
        "published_at": raw_item.published_at,
        "published_at_precision": raw_item.published_at_precision,
        "first_seen_at": raw_item.first_seen_at,
        "is_backfill": is_backfill,
        "extraction_confidence": confidence,
        "thumbnail_url": thumbnail_url or "",
        "date_confidence": date_quality["date_confidence"],
        "is_date_suspect": date_quality["is_date_suspect"],
        "date_suspect_reason": date_quality["date_suspect_reason"],
    }

    with transaction.atomic():
        created = False
        if existing:
            for key, value in fields.items():
                setattr(existing, key, value)
            existing.save()
            news_item = existing
        else:
            try:
                news_item = NewsItem.objects.create(raw_item=raw_item, **fields)
                created = True
            except IntegrityError:
                news_item = NewsItem.objects.filter(canonical_url=raw_item.canonical_url).first()
                if news_item is None:
                    news_item = NewsItem.objects.get(raw_item=raw_item)
                created = False

        _sync_franchises(news_item, primary_franchise_matches)
        _link_issue(news_item)
    return news_item, created


def recalculate_news_item(news_item: NewsItem) -> NewsItem | None:
    updated, _created = process_raw_item(news_item.raw_item, recalculate=True)
    return updated


def _entity_mentions_payload(franchise_matches: list) -> list[dict[str, object]]:
    return [
        {
            "name": match.franchise.name,
            "slug": match.franchise.slug,
            "matched_alias": match.matched_alias,
            "confidence_score": match.confidence_score,
            "is_primary": match.is_primary,
        }
        for match in franchise_matches
    ]


def _payload_from_feed_entry(source: Source, entry) -> dict[str, Any]:
    title = normalize_whitespace(entry.get("title", ""))
    link = _entry_link(entry)
    raw_html = _entry_content(entry)
    raw_text = strip_html(raw_html)
    raw_published_at_text = entry.get("published", "") or entry.get("updated", "") or entry.get("created", "")
    published_at = _entry_datetime(entry, source=source)
    metadata = {
        "feed_id": entry.get("id") or entry.get("guid", ""),
        "tags": [tag.get("term") for tag in entry.get("tags", []) if tag.get("term")],
        "thumbnail_url": _entry_thumbnail(entry),
        **_source_lineage_metadata(source, title=title, url=link),
    }
    return {
        "source": source,
        "title": title,
        "url": link,
        "author": entry.get("author", ""),
        "published_at": published_at,
        "raw_published_at_text": raw_published_at_text,
        "published_at_precision": precision_for_datetime(published_at, raw_published_at_text),
        "raw_html": raw_html,
        "raw_text": raw_text,
        "metadata": metadata,
    }


def _entry_link(entry) -> str:
    if entry.get("link"):
        return entry.get("link", "")
    for link in entry.get("links", []) or []:
        if link.get("rel") in {"alternate", None} and link.get("href"):
            return link["href"]
    return entry.get("id") or entry.get("guid", "")


def _source_lineage_metadata(source: Source, *, title: str, url: str) -> dict[str, str]:
    if source.trust_type == "official":
        original_source = source.name
        display_source = source.name
        transfer_source = ""
    elif source.source_type == SourceType.REDDIT_RSS:
        original_source = _infer_original_source(source, title=title, url=url)
        display_source = original_source or source.name
        transfer_source = "Reddit 게시물"
    else:
        original_source = ""
        display_source = source.name
        transfer_source = ""
    return {
        "original_source": original_source or "",
        "display_source": display_source,
        "transfer_source": transfer_source,
        "collection_source": source.name,
    }


def _infer_original_source(source: Source, *, title: str, url: str) -> str:
    if source.source_type != SourceType.REDDIT_RSS:
        return ""
    candidates = [
        "Bloomberg",
        "Reuters",
        "Nikkei",
        "Famitsu",
        "VGC",
        "Nintendo Life",
        "Gematsu",
        "IGN",
        "Eurogamer",
    ]
    for candidate in candidates:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(candidate)}(?![A-Za-z0-9])", title, flags=re.IGNORECASE):
            return candidate
    prefix = re.match(r"^\s*\[?([A-Z][A-Za-z0-9 &.'-]{2,40})\]?\s*[:\-–—|]", title)
    if prefix:
        value = normalize_whitespace(prefix.group(1))
        if value.casefold() not in {"rumor", "leak", "nintendo", "switch"}:
            return value
    return ""


def _html_payloads_from_selectors(source: Source, soup: BeautifulSoup, page_html: str) -> list[dict[str, Any]]:
    config = adapter_config(source)
    payloads: list[dict[str, Any]] = []
    base_url = config.get("base_url") or source.url
    link_attr = config.get("link_attr", "href")
    date_attr = config.get("date_attr") or "datetime"
    thumbnail_attr = config.get("thumbnail_attr", "src")

    for item in soup.select(config["item_selector"]):
        title_el = item.select_one(config.get("title_selector", "")) if config.get("title_selector") else None
        link_el = item.select_one(config.get("link_selector", "")) if config.get("link_selector") else item.find("a", href=True)
        date_el = item.select_one(config.get("date_selector", "")) if config.get("date_selector") else None
        title = _select_text(title_el) if title_el else _best_link_title(link_el, config.get("generic_title_selector"))
        href = _select_attr(link_el, link_attr) if link_el else ""
        if not title or not href:
            continue
        url = normalize_url(href, base_url=base_url)
        if not _passes_text_filters(source, title, url):
            continue
        summary_text = _select_text(item.select_one(config["summary_selector"])) if config.get("summary_selector") else ""
        text_el = item.select_one(config["text_selector"]) if config.get("text_selector") else item
        raw_text = _combined_text(summary_text, _select_text(text_el))
        author = _select_text(item.select_one(config["author_selector"])) if config.get("author_selector") else ""
        thumbnail_el = item.select_one(config["thumbnail_selector"]) if config.get("thumbnail_selector") else None
        thumbnail_url = normalize_url(_select_attr(thumbnail_el, thumbnail_attr), base_url=base_url) if thumbnail_el else ""
        date_value = _select_attr(date_el, date_attr) or _select_text(date_el)
        published_at = _parse_datetime(date_value, source=source)
        payloads.append(
            {
                "source": source,
                "title": title,
                "url": url,
                "author": author,
                "published_at": published_at,
                "raw_published_at_text": date_value,
                "published_at_precision": precision_for_datetime(published_at, date_value),
                "raw_html": str(item),
                "raw_text": raw_text,
                "metadata": {
                    "html_source": "selector",
                    "page_url": source.url,
                    "thumbnail_url": thumbnail_url,
                    "page_html_excerpt": page_html[:500],
                    **_source_lineage_metadata(source, title=title, url=url),
                },
            }
        )
    return payloads


def _html_payloads_generic(source: Source, soup: BeautifulSoup, page_html: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    seen: set[str] = set()
    config = adapter_config(source)
    base_url = config.get("base_url") or source.url
    for link in soup.find_all("a", href=True):
        title = _best_link_title(link, config.get("generic_title_selector"))
        href = link.get("href", "")
        if not title or not href:
            continue
        url = normalize_url(href, base_url=base_url)
        if url in seen or not _is_likely_article_link(source, title, url) or not _passes_text_filters(source, title, url):
            continue
        seen.add(url)
        parent = link.find_parent(["article", "li", "div", "section"]) or link
        date_el = parent.find("time")
        date_value = _select_attr(date_el, "datetime") or _select_text(date_el) or _select_text(parent)
        published_at = _parse_datetime(date_value, source=source)
        thumbnail_el = parent.find("img")
        thumbnail_url = normalize_url(_select_attr(thumbnail_el, "src"), base_url=base_url) if thumbnail_el else ""
        payloads.append(
            {
                "source": source,
                "title": truncate(title, 500),
                "url": url,
                "author": "",
                "published_at": published_at,
                "raw_published_at_text": date_value,
                "published_at_precision": precision_for_datetime(published_at, date_value),
                "raw_html": str(parent),
                "raw_text": normalize_whitespace(parent.get_text(" ")),
                "metadata": {
                    "html_source": "generic",
                    "page_url": source.url,
                    "thumbnail_url": thumbnail_url,
                    "page_html_excerpt": page_html[:500],
                    **_source_lineage_metadata(source, title=title, url=url),
                },
            }
        )
    return payloads


def _html_payloads_from_embedded_json(source: Source, soup: BeautifulSoup, page_html: str) -> list[dict[str, Any]]:
    config = adapter_config(source)
    script = soup.select_one(config["embedded_json_selector"])
    if script is None:
        return []

    raw_json = script.string or script.get_text("", strip=True)
    if not raw_json:
        return []

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse embedded JSON source=%s error=%s", source.slug, exc)
        return []

    item_type = config.get("embedded_json_item_type")
    title_fields = config.get("embedded_json_title_fields") or ["title"]
    url_fields = config.get("embedded_json_url_fields") or ['url({"relative":true})', "url", "href", "link"]
    summary_fields = config.get("embedded_json_summary_fields") or ['body.text({"characterLimit":250})', "summary", "description"]
    date_fields = config.get("embedded_json_date_fields") or ["publishDate", "publishedAt", "datePublished"]
    author_fields = config.get("embedded_json_author_fields") or ["author.name", "author"]

    payloads: list[dict[str, Any]] = []
    seen: set[str] = set()
    base_url = config.get("base_url") or source.url

    for item in _walk_json_dicts(data):
        if item_type and item.get("__typename") != item_type:
            continue
        title = normalize_whitespace(_json_first_value(item, title_fields))
        href = normalize_whitespace(_json_first_value(item, url_fields))
        if not title or not href:
            continue
        url = normalize_url(href, base_url=base_url)
        if url in seen or not _passes_text_filters(source, title, url):
            continue
        seen.add(url)
        summary = normalize_whitespace(_json_first_value(item, summary_fields))
        author = normalize_whitespace(_json_first_value(item, author_fields))
        date_value = normalize_whitespace(_json_first_value(item, date_fields))
        published_at = _parse_datetime(date_value, source=source)
        payloads.append(
            {
                "source": source,
                "title": title,
                "url": url,
                "author": author,
                "published_at": published_at,
                "raw_published_at_text": date_value,
                "published_at_precision": precision_for_datetime(published_at, date_value),
                "raw_html": json.dumps(item, ensure_ascii=False)[:5000],
                "raw_text": summary,
                "metadata": {
                    "html_source": "embedded_json",
                    "json_type": item_type or "",
                    "page_url": source.url,
                    "page_html_excerpt": page_html[:500],
                    **_source_lineage_metadata(source, title=title, url=url),
                },
            }
        )
    return payloads


def _walk_json_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json_dicts(child)


def _json_first_value(item: dict[str, Any], paths: list[str]) -> str:
    for path in paths:
        value = _json_path_value(item, str(path))
        if value not in (None, ""):
            return str(value)
    return ""


def _json_path_value(item: dict[str, Any], path: str):
    value: Any = item
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    if isinstance(value, (dict, list)):
        return None
    return value


def _select_text(element) -> str:
    if element is None:
        return ""
    return normalize_whitespace(element.get_text(" "))


def _best_link_title(element, selectors: str | None = None) -> str:
    if element is None:
        return ""
    selector_list = [part.strip() for part in str(selectors or "").split(",") if part.strip()] or [
        "h1",
        "h2",
        "h3",
        "h4",
        "[class*='title']",
        "[class*='headline']",
        "[data-testid*='title']",
        "[data-testid*='headline']",
    ]
    for selector in selector_list:
        candidate = element.select_one(selector) if hasattr(element, "select_one") else None
        text = _select_text(candidate)
        if len(text) >= 12:
            return text
    return normalize_whitespace(element.get_text(" "))


def _select_attr(element, attr: str) -> str:
    if element is None or not attr:
        return ""
    value = element.get(attr, "")
    if isinstance(value, list):
        return " ".join(value)
    return normalize_whitespace(str(value))


def _combined_text(*parts: str) -> str:
    combined: list[str] = []
    for part in parts:
        clean = normalize_whitespace(part)
        if clean and clean not in combined:
            combined.append(clean)
    return normalize_whitespace(" ".join(combined))


def _passes_text_filters(source: Source, title: str, url: str) -> bool:
    config = adapter_config(source)
    lowered_title = title.lower()
    lowered_url = url.lower()
    include_keywords = [str(value).lower() for value in config.get("title_include_keywords", [])]
    exclude_keywords = [str(value).lower() for value in config.get("title_exclude_keywords", [])]
    exclude_exact = DEFAULT_TITLE_EXCLUDE_EXACT | {
        str(value).strip().lower() for value in config.get("title_exclude_exact", []) if str(value).strip()
    }
    url_includes = [str(value).lower() for value in config.get("url_include_patterns", [])]
    url_excludes = [str(value).lower() for value in config.get("url_exclude_patterns", DEFAULT_EXCLUDE_PATTERNS)]

    if lowered_title.strip() in exclude_exact:
        return False
    if include_keywords and not any(keyword in lowered_title for keyword in include_keywords):
        return False
    if exclude_keywords and any(keyword in lowered_title for keyword in exclude_keywords):
        return False
    if url_includes and not any(pattern in lowered_url for pattern in url_includes):
        return False
    if any(pattern in lowered_url for pattern in url_excludes):
        return False
    return True


def _store_payload(result: CollectResult, payload: dict[str, Any], *, dry_run: bool = False) -> None:
    source = payload["source"]
    title = normalize_whitespace(payload.get("title", ""))
    url = normalize_url(payload.get("url", ""), base_url=source.url)
    if not title or not url:
        result.skipped_count += 1
        return
    if not _passes_text_filters(source, title, url):
        result.skipped_count += 1
        return
    result.found_count += 1
    canonical_url = normalize_url(url)
    canonical_url_hash = create_url_hash(canonical_url)
    content_hash = content_hash_for(title, canonical_url)
    published_at = payload.get("published_at")
    date_quality = date_quality_update_fields(published_at)
    rejection_reason = article_rejection_reason(
        title=title,
        url=canonical_url,
        raw_text=payload.get("raw_text", "") or "",
        published_at=published_at,
        source=source,
    )
    extraction_confidence = extraction_confidence_for(
        title=title,
        url=canonical_url,
        raw_text=payload.get("raw_text", "") or "",
        published_at=published_at,
        source=source,
    )
    if rejection_reason:
        result.skipped_count += 1

    if dry_run:
        result.dry_run_items.append(
            {
                "title": title,
                "url": url,
                "canonical_url": canonical_url,
                "rejection_reason": rejection_reason,
                "date_confidence": date_quality["date_confidence"],
                "is_date_suspect": date_quality["is_date_suspect"],
                "date_suspect_reason": date_quality["date_suspect_reason"],
            }
        )
        return

    existing = find_existing_raw_item(
        source=source,
        title=title,
        canonical_url=canonical_url,
        content_hash=content_hash,
    )
    if existing:
        result.duplicate_count += 1
        update_fields: list[str] = []
        if not existing.canonical_url_hash and canonical_url_hash:
            existing.canonical_url_hash = canonical_url_hash
            update_fields.append("canonical_url_hash")
        if not existing.rejection_reason and rejection_reason:
            existing.rejection_reason = rejection_reason
            update_fields.append("rejection_reason")
        if existing.extraction_confidence != extraction_confidence:
            existing.extraction_confidence = extraction_confidence
            update_fields.append("extraction_confidence")
        for field, value in date_quality.items():
            if getattr(existing, field) != value:
                setattr(existing, field, value)
                update_fields.append(field)
        if update_fields:
            existing.save(update_fields=update_fields)
        if not hasattr(existing, "news_item"):
            result.raw_items.append(existing)
        return

    try:
        raw_item = RawItem.objects.create(
            source=source,
            title=title,
            url=url,
            canonical_url=canonical_url,
            author=payload.get("author", "") or "",
            published_at=published_at,
            raw_published_at_text=payload.get("raw_published_at_text", "") or "",
            published_at_precision=payload.get("published_at_precision") or PublishedAtPrecision.UNKNOWN,
            raw_html=payload.get("raw_html", "") or "",
            raw_text=payload.get("raw_text", "") or "",
            content_hash=content_hash,
            canonical_url_hash=canonical_url_hash,
            extraction_confidence=extraction_confidence,
            rejection_reason=rejection_reason,
            date_confidence=date_quality["date_confidence"],
            is_date_suspect=date_quality["is_date_suspect"],
            date_suspect_reason=date_quality["date_suspect_reason"],
            metadata=payload.get("metadata", {}) or {},
        )
    except IntegrityError:
        result.duplicate_count += 1
        raw_item = RawItem.objects.filter(canonical_url=canonical_url).first()
        if raw_item and not hasattr(raw_item, "news_item"):
            result.raw_items.append(raw_item)
        return

    result.created_count += 1
    result.raw_items.append(raw_item)


def _sync_franchises(news_item: NewsItem, franchise_matches: list) -> None:
    franchises = [match.franchise for match in franchise_matches]
    news_item.franchise_links.exclude(franchise__in=franchises).delete()
    for match in franchise_matches:
        link, created = NewsItemFranchise.objects.get_or_create(
            news_item=news_item,
            franchise=match.franchise,
            defaults={
                "matched_alias": match.matched_alias,
                "confidence_score": match.confidence_score,
                "is_primary": match.is_primary,
            },
        )
        if created and link.is_primary != match.is_primary:
            link.is_primary = match.is_primary
            link.save(update_fields=["is_primary"])
        if not created and (
            link.matched_alias != match.matched_alias
            or link.confidence_score != match.confidence_score
            or link.is_primary != match.is_primary
        ):
            link.matched_alias = match.matched_alias
            link.confidence_score = match.confidence_score
            link.is_primary = match.is_primary
            link.save(update_fields=["matched_alias", "confidence_score", "is_primary"])


def _link_issue(news_item: NewsItem) -> Issue:
    match = _find_related_issue(news_item)
    now = timezone.now()

    if match is None:
        status = IssueStatus.CONFIRMED if news_item.trust_label == TrustLabel.OFFICIAL else IssueStatus.DEVELOPING
        if news_item.trust_label == TrustLabel.RUMOR or news_item.category in {NewsCategory.RUMOR, NewsCategory.LEAK}:
            status = IssueStatus.RUMOR
        issue = Issue.objects.create(
            title=news_item.title,
            canonical_topic=canonical_topic_from_title(news_item.title),
            status=status,
            confidence_score=news_item.confidence_score,
            first_seen_at=news_item.first_seen_at,
            last_updated_at=now,
            official_confirmed_at=now if status == IssueStatus.CONFIRMED else None,
        )
        relation = IssueRelation.SAME_STORY
        relation_confidence = 1.0
        explanation = "새 이슈 생성"
        decision_debug = {
            "decision": "new_issue",
            "decision_reason": "no_existing_issue_with_strong_signals",
            "strong_signals": [],
            "weak_signals": [],
            "title_similarity": 0.0,
            "shared_entities": [],
            "shared_primary_franchises": [],
        }
    else:
        issue = match.issue
        relation = match.relation
        relation_confidence = match.confidence
        explanation = match.explanation
        decision_debug = match.decision_debug
        if relation in {IssueRelation.CONFIRMATION, IssueRelation.OFFICIAL_CONFIRMATION}:
            issue.status = IssueStatus.CONFIRMED
            issue.official_confirmed_at = news_item.published_at or now
        issue.confidence_score = max(issue.confidence_score, news_item.confidence_score)
        issue.last_updated_at = now
        issue.save(update_fields=["status", "confidence_score", "last_updated_at", "official_confirmed_at", "updated_at"])

    link, created = NewsItemIssue.objects.get_or_create(
        news_item=news_item,
        issue=issue,
        defaults={
            "relation": relation,
            "relation_confidence": relation_confidence,
            "explanation": explanation,
            "decision_debug": decision_debug,
        },
    )
    if not created and (
        link.relation != relation
        or link.relation_confidence != relation_confidence
        or link.explanation != explanation
        or link.decision_debug != decision_debug
    ):
        link.relation = relation
        link.relation_confidence = relation_confidence
        link.explanation = explanation
        link.decision_debug = decision_debug
        link.save(update_fields=["relation", "relation_confidence", "explanation", "decision_debug"])
    rebuild_issue_title(issue)
    refresh_issue_review_status(issue)
    return issue


def _find_related_issue(news_item: NewsItem) -> IssueMatch | None:
    tokens = _specific_issue_tokens(news_item.title)
    if not tokens:
        return None
    cutoff = timezone.now() - timedelta(days=14)
    best_match: IssueMatch | None = None
    best_score = 0.0
    news_franchise_ids = {link.franchise_id for link in news_item.franchise_links.all() if link.is_primary}
    candidates = Issue.objects.filter(last_updated_at__gte=cutoff).prefetch_related(
        "news_links__news_item__franchise_links",
    )[:200]
    for issue in candidates:
        issue_tokens = _specific_issue_tokens(f"{issue.canonical_topic} {issue.title}")
        if not issue_tokens:
            continue
        overlap = len(tokens & issue_tokens)
        shared_tokens = sorted(tokens & issue_tokens)
        issue_items = [link.news_item for link in issue.news_links.all()]
        issue_links = list(issue.news_links.all())
        issue_franchise_ids = {
            franchise_link.franchise_id
            for item in issue_items
            for franchise_link in item.franchise_links.all()
            if franchise_link.is_primary
        }
        shared_franchise = bool(news_franchise_ids and news_franchise_ids & issue_franchise_ids)
        duplicate_url = bool(
            news_item.canonical_url
            and any(item.pk != news_item.pk and item.canonical_url == news_item.canonical_url for item in issue_items)
        )
        same_source = any(item.source_id == news_item.source_id for item in issue_items)
        confirmation_candidate = (
            news_item.trust_label == TrustLabel.OFFICIAL
            and issue.status in {IssueStatus.RUMOR, IssueStatus.DEVELOPING}
        )
        followup_candidate = (
            news_item.trust_label == TrustLabel.REPORTED
            and any(item.trust_label == TrustLabel.OFFICIAL for item in issue_items)
        )
        similarity = difflib.SequenceMatcher(None, normalize_title(news_item.title), normalize_title(issue.title)).ratio()
        issue_relevance = max([getattr(item, "nintendo_relevance_score", 2) for item in issue_items] or [2])
        low_relevance = min(getattr(news_item, "nintendo_relevance_score", 2), issue_relevance) < 3
        broad_collection = news_item.content_type in {NewsContentType.ROUNDUP, NewsContentType.LIST_PAGE, NewsContentType.HUB_PAGE} or any(
            item.content_type in {NewsContentType.ROUNDUP, NewsContentType.LIST_PAGE, NewsContentType.HUB_PAGE}
            for item in issue_items
        )
        strong_signals: list[str] = []
        weak_signals: list[str] = []
        if duplicate_url:
            strong_signals.append("canonical_url")
        if similarity >= 0.82:
            strong_signals.append(f"title_similarity={similarity:.2f}")
        elif similarity >= 0.68:
            weak_signals.append(f"title_similarity={similarity:.2f}")
        if overlap >= 2:
            if low_relevance or broad_collection:
                weak_signals.append(f"shared_specific_tokens={shared_tokens[:6]}")
            else:
                strong_signals.append(f"shared_specific_tokens={shared_tokens[:6]}")
        elif overlap:
            weak_signals.append(f"shared_specific_tokens={shared_tokens[:6]}")
            if confirmation_candidate:
                strong_signals.append("shared_specific_token_confirmation")
        if shared_franchise:
            weak_signals.append("shared_primary_franchise")
            if overlap >= 2 and not low_relevance and not broad_collection:
                strong_signals.append("shared_primary_franchise_with_entities")
            if confirmation_candidate and overlap >= 1:
                strong_signals.append("shared_primary_franchise_confirmation")
        confirmation_context = confirmation_candidate and _has_confirmation_context(f"{news_item.title} {news_item.summary_original}")
        if confirmation_context:
            strong_signals.append("official_confirmation_context")
        if same_source:
            weak_signals.append("same_source")
        if news_item.source.slug in {"nintendo-us-whats-new", "nintendo-uk-news"}:
            weak_signals.append("official_listing_source")
        if low_relevance:
            weak_signals.append("low_nintendo_relevance")
        if broad_collection:
            weak_signals.append("broad_collection_or_list_page")

        same_story = duplicate_url or (
            not low_relevance
            and not broad_collection
            and len(strong_signals) >= 2
            and (similarity >= 0.72 or overlap >= 2)
        )
        confirmation = confirmation_candidate and (
            not low_relevance
            and not broad_collection
            and len(strong_signals) >= 2
            and (overlap >= 1 or similarity >= 0.68 or "official_confirmation_context" in strong_signals)
        )
        decision_debug = {
            "strong_signals": strong_signals,
            "weak_signals": weak_signals,
            "title_similarity": round(similarity, 3),
            "shared_entities": shared_tokens[:8],
            "shared_primary_franchises": sorted(news_franchise_ids & issue_franchise_ids),
            "overlap": overlap,
            "candidate_issue_id": issue.pk,
        }
        follow_up = (
            followup_candidate
            and not low_relevance
            and not broad_collection
            and len(strong_signals) >= 2
            and (overlap >= 1 or similarity >= 0.72)
        )
        if not same_story and not confirmation and not follow_up:
            decision_debug.update(
                {
                    "decision": "new_issue",
                    "decision_reason": "only_weak_or_generic_signals",
                }
            )
            logger.debug(
                "Issue not linked item=%s issue=%s strong=%s weak=%s reason=%s",
                news_item.pk,
                issue.pk,
                strong_signals,
                weak_signals,
                "Only weak or generic signals matched",
            )
            continue

        score = overlap / max(min(len(tokens), len(issue_tokens)), 1)
        if shared_franchise:
            score += 0.1
        score += min(similarity * 0.2, 0.2)
        if confirmation_candidate:
            score += 0.15
        if "official_confirmation_context" in strong_signals:
            score += 0.25
        if confirmation:
            score = max(score, 0.65)

        if follow_up:
            score = max(score, 0.65)

        threshold = 0.5 if confirmation or follow_up else 0.72
        if duplicate_url:
            score = max(score, 0.95)
        if score > best_score and score >= threshold:
            if duplicate_url:
                relation = IssueRelation.SOURCE_DUPLICATE
            elif confirmation:
                relation = IssueRelation.OFFICIAL_CONFIRMATION
            elif follow_up:
                relation = IssueRelation.FOLLOWUP
            else:
                relation = IssueRelation.SAME_STORY
            decision_debug.update(
                {
                    "decision": "linked",
                    "decision_reason": f"strong_signal_count={len(strong_signals)}",
                    "relation": relation,
                    "score": round(score, 3),
                    "candidate_link_count": len(issue_links),
                }
            )
            explanation = (
                f"decision=linked relation={relation}; strong_signals={strong_signals}; "
                f"weak_signals={weak_signals}; shared_entities={shared_tokens[:8]}; "
                f"overlap={overlap}; title_similarity={similarity:.2f}; score={score:.2f}"
            )
            best_match = IssueMatch(
                issue=issue,
                relation=relation,
                confidence=round(min(score, 1.0), 3),
                explanation=explanation,
                decision_debug=decision_debug,
            )
            best_score = score
    return best_match


def _specific_issue_tokens(value: str) -> set[str]:
    return {token for token in tokenize_topic(value) if token not in BROAD_ISSUE_TOKENS}


def _categories_related(left: str, right: str) -> bool:
    if left == right:
        return True
    return any(left in group and right in group for group in RELATED_CATEGORY_GROUPS)


def _entry_content(entry) -> str:
    if entry.get("content"):
        content = entry.get("content")
        if content and content[0].get("value"):
            return content[0].get("value", "")
    return entry.get("summary", "") or entry.get("description", "") or ""


def _entry_thumbnail(entry) -> str:
    thumbnails = entry.get("media_thumbnail") or []
    if thumbnails and thumbnails[0].get("url"):
        return thumbnails[0]["url"]
    media_content = entry.get("media_content") or []
    if media_content and media_content[0].get("url"):
        return media_content[0]["url"]
    return ""


def _entry_datetime(entry, *, source: Source | None = None):
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if parsed:
            return timezone.datetime.fromtimestamp(calendar.timegm(parsed), tz=datetime_timezone.utc)
    for key in ("published", "updated", "created"):
        parsed = _parse_datetime(entry.get(key, ""), source=source)
        if parsed:
            return parsed
    return None


def _parse_datetime(value: str, *, source: Source | None = None):
    if not value:
        return None
    try:
        parsed = date_parser.parse(value, fuzzy=True)
    except (ValueError, TypeError, OverflowError):
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone=_source_timezone(source))
    return parsed


def _is_likely_article_link(source: Source, title: str, url: str) -> bool:
    config = adapter_config(source)
    parsed_source = urlparse(source.url)
    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"}:
        return False
    if not config.get("allow_external_links", False) and parsed_source.netloc and parsed_source.netloc != parsed_url.netloc:
        return False
    min_title_length = int(_bounded_float(config.get("title_min_length"), 8, minimum=3, maximum=80))
    if len(title) < min_title_length:
        return False

    include_patterns = config.get("url_include_patterns") or [
        "/news",
        "/article",
        "/articles",
        "/whatsnew",
        "/release",
        "/schedule",
    ]
    exclude_patterns = config.get("url_exclude_patterns") or DEFAULT_EXCLUDE_PATTERNS
    lowered_path = parsed_url.path.lower()
    if any(pattern.lower() in lowered_path for pattern in exclude_patterns):
        return False
    if any(pattern.lower() in lowered_path for pattern in include_patterns):
        return True
    return len(title.split()) >= 4 and any(word in title.lower() for word in ["nintendo", "switch", "direct"])


def _source_timezone(source: Source | None):
    name = adapter_config(source).get("timezone") if source is not None else None
    if not name:
        return timezone.get_current_timezone()
    try:
        return ZoneInfo(str(name))
    except ZoneInfoNotFoundError:
        logger.warning("Unknown source timezone source=%s timezone=%s", getattr(source, "slug", ""), name)
        return timezone.get_current_timezone()


def _has_confirmation_context(value: str) -> bool:
    normalized = normalize_title(value)
    return any(
        marker in normalized
        for marker in [
            "official",
            "confirmed",
            "announced",
            "release date",
            "launch",
            "revealed",
            "공식",
            "확정",
            "발표",
            "공개",
            "출시",
            "발매",
        ]
    )


def _error_text(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response else "unknown"
        return f"HTTP {status}: {str(exc)[:500]}"
    return str(exc)[:1000]
