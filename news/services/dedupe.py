from __future__ import annotations

from django.db.models import Q

from news.models import NewsItem, RawItem, Source

from .text import create_content_hash, normalize_title, normalize_url


def canonicalize_url(url: str, base_url: str | None = None) -> str:
    return normalize_url(url, base_url=base_url)


def normalized_title(title: str) -> str:
    return normalize_title(title)


def content_hash_for(title: str, canonical_url: str = "") -> str:
    return create_content_hash(title, canonical_url)


def find_existing_raw_item(
    *,
    source: Source,
    title: str,
    canonical_url: str,
    content_hash: str,
) -> RawItem | None:
    if canonical_url:
        existing = RawItem.objects.filter(canonical_url=canonical_url).first()
        if existing:
            return existing
    existing = RawItem.objects.filter(Q(content_hash=content_hash) | Q(source=source, title__iexact=title)).first()
    if existing:
        return existing

    norm_title = normalize_title(title)
    for raw_item in RawItem.objects.filter(source=source).only("id", "title").order_by("-first_seen_at")[:500]:
        if normalize_title(raw_item.title) == norm_title:
            return raw_item
    return None


def find_existing_news_item(*, raw_item: RawItem, canonical_url: str, title: str) -> NewsItem | None:
    if hasattr(raw_item, "news_item"):
        return raw_item.news_item
    if canonical_url:
        existing = NewsItem.objects.filter(canonical_url=canonical_url).first()
        if existing:
            return existing
    norm_title = normalize_title(title)
    return NewsItem.objects.filter(normalized_title=norm_title, source=raw_item.source).first()
