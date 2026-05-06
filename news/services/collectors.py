from __future__ import annotations

import calendar
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta, timezone as datetime_timezone
from typing import Any
from urllib.parse import urljoin, urlparse

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
    NewsItem,
    NewsItemFranchise,
    NewsItemIssue,
    RawItem,
    Source,
    SourceType,
    TrustLabel,
)

from .classifier import classify_item, detect_franchises
from .dedupe import content_hash_for, find_existing_news_item, find_existing_raw_item
from .importance import calculate_importance
from .summarizer import summarize_item
from .text import (
    canonical_topic_from_title,
    normalize_title,
    normalize_url,
    normalize_whitespace,
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
    config = source.config or {}
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
    for name, value in (source.config or {}).get("http_headers", {}).items():
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
        source.save(update_fields=["last_error", "last_new_items_count", "updated_at"])
        return CollectResult(source=source, errors=[error], elapsed_seconds=elapsed)

    result.elapsed_seconds = time.monotonic() - started
    source.last_success_at = timezone.now()
    if result.errors:
        source.last_error = "; ".join(result.errors)[:3000]
    else:
        source.last_error = ""
    source.last_new_items_count = result.created_count
    source.save(update_fields=["last_success_at", "last_error", "last_new_items_count", "updated_at"])
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
    config = source.config or {}
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
    config = source.config or {}
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


def process_raw_item(raw_item: RawItem, *, recalculate: bool = False) -> tuple[NewsItem, bool]:
    existing = find_existing_news_item(
        raw_item=raw_item,
        canonical_url=raw_item.canonical_url or "",
        title=raw_item.title,
    )
    if existing and not recalculate:
        return existing, False

    classification = classify_item(raw_item.source, raw_item.title, raw_item.raw_text)
    franchises = detect_franchises(raw_item.title, raw_item.raw_text)
    importance = calculate_importance(
        source=raw_item.source,
        title=raw_item.title,
        raw_text=raw_item.raw_text,
        tags=classification.tags,
        franchises=franchises,
    )
    summary_ko = summarize_item(
        source=raw_item.source,
        title=raw_item.title,
        raw_text=raw_item.raw_text,
        classification=classification,
    )
    thumbnail_url = raw_item.metadata.get("thumbnail_url", "") if raw_item.metadata else ""

    fields = {
        "source": raw_item.source,
        "title": raw_item.title,
        "normalized_title": normalize_title(raw_item.title),
        "url": raw_item.url,
        "canonical_url": raw_item.canonical_url,
        "summary_ko": summary_ko,
        "summary_original": truncate(raw_item.raw_text, 1500),
        "trust_label": classification.trust_label,
        "category": classification.category,
        "detected_tags": classification.tags,
        "confidence_score": classification.confidence_score,
        "importance_score": importance,
        "region": raw_item.source.region,
        "language": raw_item.source.language,
        "published_at": raw_item.published_at,
        "first_seen_at": raw_item.first_seen_at,
        "thumbnail_url": thumbnail_url or "",
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

        _sync_franchises(news_item, franchises)
        _link_issue(news_item)
    return news_item, created


def recalculate_news_item(news_item: NewsItem) -> NewsItem:
    updated, _created = process_raw_item(news_item.raw_item, recalculate=True)
    return updated


def _payload_from_feed_entry(source: Source, entry) -> dict[str, Any]:
    title = normalize_whitespace(entry.get("title", ""))
    link = _entry_link(entry)
    raw_html = _entry_content(entry)
    raw_text = strip_html(raw_html)
    metadata = {
        "feed_id": entry.get("id") or entry.get("guid", ""),
        "tags": [tag.get("term") for tag in entry.get("tags", []) if tag.get("term")],
        "thumbnail_url": _entry_thumbnail(entry),
    }
    return {
        "source": source,
        "title": title,
        "url": link,
        "author": entry.get("author", ""),
        "published_at": _entry_datetime(entry),
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


def _html_payloads_from_selectors(source: Source, soup: BeautifulSoup, page_html: str) -> list[dict[str, Any]]:
    config = source.config or {}
    payloads: list[dict[str, Any]] = []
    base_url = config.get("base_url") or source.url
    link_attr = config.get("link_attr", "href")
    date_attr = config.get("date_attr") or "datetime"
    thumbnail_attr = config.get("thumbnail_attr", "src")

    for item in soup.select(config["item_selector"]):
        title_el = item.select_one(config.get("title_selector", "")) if config.get("title_selector") else None
        link_el = item.select_one(config.get("link_selector", "")) if config.get("link_selector") else item.find("a", href=True)
        date_el = item.select_one(config.get("date_selector", "")) if config.get("date_selector") else None
        title = _select_text(title_el) if title_el else _select_text(link_el)
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
        payloads.append(
            {
                "source": source,
                "title": title,
                "url": url,
                "author": author,
                "published_at": _parse_datetime(date_value),
                "raw_html": str(item),
                "raw_text": raw_text,
                "metadata": {
                    "html_source": "selector",
                    "page_url": source.url,
                    "thumbnail_url": thumbnail_url,
                    "page_html_excerpt": page_html[:500],
                },
            }
        )
    return payloads


def _html_payloads_generic(source: Source, soup: BeautifulSoup, page_html: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    seen: set[str] = set()
    config = source.config or {}
    base_url = config.get("base_url") or source.url
    for link in soup.find_all("a", href=True):
        title = normalize_whitespace(link.get_text(" "))
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
        thumbnail_el = parent.find("img")
        thumbnail_url = normalize_url(_select_attr(thumbnail_el, "src"), base_url=base_url) if thumbnail_el else ""
        payloads.append(
            {
                "source": source,
                "title": truncate(title, 500),
                "url": url,
                "author": "",
                "published_at": _parse_datetime(date_value),
                "raw_html": str(parent),
                "raw_text": normalize_whitespace(parent.get_text(" ")),
                "metadata": {
                    "html_source": "generic",
                    "page_url": source.url,
                    "thumbnail_url": thumbnail_url,
                    "page_html_excerpt": page_html[:500],
                },
            }
        )
    return payloads


def _html_payloads_from_embedded_json(source: Source, soup: BeautifulSoup, page_html: str) -> list[dict[str, Any]]:
    config = source.config or {}
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
        payloads.append(
            {
                "source": source,
                "title": title,
                "url": url,
                "author": author,
                "published_at": _parse_datetime(date_value),
                "raw_html": json.dumps(item, ensure_ascii=False)[:5000],
                "raw_text": summary,
                "metadata": {
                    "html_source": "embedded_json",
                    "json_type": item_type or "",
                    "page_url": source.url,
                    "page_html_excerpt": page_html[:500],
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
    config = source.config or {}
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
    result.found_count += 1
    canonical_url = normalize_url(url)
    content_hash = content_hash_for(title, canonical_url)

    if dry_run:
        result.dry_run_items.append({"title": title, "url": url, "canonical_url": canonical_url})
        return

    existing = find_existing_raw_item(
        source=source,
        title=title,
        canonical_url=canonical_url,
        content_hash=content_hash,
    )
    if existing:
        result.duplicate_count += 1
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
            published_at=payload.get("published_at"),
            raw_html=payload.get("raw_html", "") or "",
            raw_text=payload.get("raw_text", "") or "",
            content_hash=content_hash,
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


def _sync_franchises(news_item: NewsItem, franchises: list) -> None:
    news_item.franchise_links.exclude(franchise__in=franchises).delete()
    for franchise in franchises:
        NewsItemFranchise.objects.get_or_create(news_item=news_item, franchise=franchise)


def _link_issue(news_item: NewsItem) -> Issue:
    issue = _find_related_issue(news_item)
    relation = IssueRelation.RELATED
    now = timezone.now()

    if issue is None:
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
    else:
        same_category = issue.news_links.filter(news_item__category=news_item.category).exists()
        relation = IssueRelation.SAME_STORY if same_category else IssueRelation.RELATED
        if news_item.trust_label == TrustLabel.OFFICIAL and issue.status in {IssueStatus.RUMOR, IssueStatus.DEVELOPING}:
            issue.status = IssueStatus.CONFIRMED
            issue.official_confirmed_at = news_item.published_at or now
            relation = IssueRelation.CONFIRMATION
        issue.confidence_score = max(issue.confidence_score, news_item.confidence_score)
        issue.last_updated_at = now
        issue.save(update_fields=["status", "confidence_score", "last_updated_at", "official_confirmed_at", "updated_at"])

    NewsItemIssue.objects.get_or_create(news_item=news_item, issue=issue, defaults={"relation": relation})
    return issue


def _find_related_issue(news_item: NewsItem) -> Issue | None:
    tokens = tokenize_topic(news_item.title)
    if not tokens:
        return None
    cutoff = timezone.now() - timedelta(days=14)
    best_issue = None
    best_score = 0.0
    news_franchise_ids = {link.franchise_id for link in news_item.franchise_links.all()}
    candidates = Issue.objects.filter(last_updated_at__gte=cutoff).prefetch_related(
        "news_links__news_item__franchise_links",
    )[:200]
    for issue in candidates:
        issue_tokens = tokenize_topic(f"{issue.canonical_topic} {issue.title}")
        if not issue_tokens:
            continue
        overlap = len(tokens & issue_tokens)
        issue_items = [link.news_item for link in issue.news_links.all()]
        same_category = any(item.category == news_item.category for item in issue_items)
        related_category = any(_categories_related(news_item.category, item.category) for item in issue_items)
        issue_franchise_ids = {
            franchise_link.franchise_id
            for item in issue_items
            for franchise_link in item.franchise_links.all()
        }
        shared_franchise = bool(news_franchise_ids and news_franchise_ids & issue_franchise_ids)
        confirmation_candidate = (
            news_item.trust_label == TrustLabel.OFFICIAL
            and issue.status in {IssueStatus.RUMOR, IssueStatus.DEVELOPING}
        )
        min_overlap = 1 if shared_franchise and (related_category or confirmation_candidate) else 2
        if overlap < min_overlap:
            continue

        score = overlap / max(min(len(tokens), len(issue_tokens)), 1)
        if same_category:
            score += 0.15
        elif related_category:
            score += 0.08
        if shared_franchise:
            score += 0.2
        if confirmation_candidate:
            score += 0.1

        threshold = 0.3 if shared_franchise and (related_category or confirmation_candidate) else 0.35
        if score > best_score and score >= threshold:
            best_issue = issue
            best_score = score
    return best_issue


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


def _entry_datetime(entry):
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if parsed:
            return timezone.datetime.fromtimestamp(calendar.timegm(parsed), tz=datetime_timezone.utc)
    for key in ("published", "updated", "created"):
        parsed = _parse_datetime(entry.get(key, ""))
        if parsed:
            return parsed
    return None


def _parse_datetime(value: str):
    if not value:
        return None
    try:
        parsed = date_parser.parse(value, fuzzy=True)
    except (ValueError, TypeError, OverflowError):
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone=timezone.get_current_timezone())
    return parsed


def _is_likely_article_link(source: Source, title: str, url: str) -> bool:
    config = source.config or {}
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
        "/topics",
        "/release",
        "/schedule",
        "/platforms",
        "/games",
    ]
    exclude_patterns = config.get("url_exclude_patterns") or DEFAULT_EXCLUDE_PATTERNS
    lowered_path = parsed_url.path.lower()
    if any(pattern.lower() in lowered_path for pattern in exclude_patterns):
        return False
    if any(pattern.lower() in lowered_path for pattern in include_patterns):
        return True
    return len(title.split()) >= 4 and any(word in title.lower() for word in ["nintendo", "switch", "direct"])


def _error_text(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response else "unknown"
        return f"HTTP {status}: {str(exc)[:500]}"
    return str(exc)[:1000]
