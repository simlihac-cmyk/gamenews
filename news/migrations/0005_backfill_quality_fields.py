from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import timedelta
from html import unescape

from django.db import migrations


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def clean_title(value: str) -> str:
    title = normalize_whitespace(value)
    title = re.sub(
        r"^(?:\d{1,2}/\d{1,2}/\d{2,4}|\d{4}[.-]\d{1,2}[.-]\d{1,2})\s*[-–—:·|]?\s*",
        "",
        title,
    )
    title = re.sub(r"\s*\bRead more\b\s*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+뉴스\s+\d{4}[.-]\d{1,2}[.-]\d{1,2}\s*$", "", title)
    return normalize_whitespace(title)


def normalize_title(title: str) -> str:
    if not title:
        return ""
    value = unicodedata.normalize("NFKC", unescape(title)).lower()
    value = value.replace("’", "'").replace("“", '"').replace("”", '"')
    value = re.sub(r"\bnintendo\s+switch\s*2\b", "switch2", value)
    value = re.sub(r"\bswitch\s*2\b", "switch2", value)
    value = re.sub(r"[_\-–—/:|·•]+", " ", value)
    value = re.sub(r"[^\w\s가-힣ぁ-ゟ゠-ヿ一-龯]", " ", value, flags=re.UNICODE)
    value = value.replace("_", " ")
    return re.sub(r"\s+", " ", value).strip()


def url_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def forwards(apps, schema_editor):
    RawItem = apps.get_model("news", "RawItem")
    NewsItem = apps.get_model("news", "NewsItem")

    for raw in RawItem.objects.all().iterator():
        updates = []
        canonical_hash = url_hash(raw.canonical_url or "")
        if raw.canonical_url_hash != canonical_hash:
            raw.canonical_url_hash = canonical_hash
            updates.append("canonical_url_hash")
        if raw.published_at and raw.published_at_precision == "unknown":
            raw.published_at_precision = "exact"
            updates.append("published_at_precision")
        if updates:
            raw.save(update_fields=updates)

    for item in NewsItem.objects.select_related("raw_item").all().iterator():
        updates = []
        title = clean_title(item.raw_item.title) or item.title
        if item.title != title:
            item.title = title
            updates.append("title")
        normalized = normalize_title(title)
        if item.normalized_title != normalized:
            item.normalized_title = normalized
            updates.append("normalized_title")
        canonical_hash = url_hash(item.canonical_url or "")
        if item.canonical_url_hash != canonical_hash:
            item.canonical_url_hash = canonical_hash
            updates.append("canonical_url_hash")
        if item.published_at and item.published_at_precision == "unknown":
            item.published_at_precision = "exact"
            updates.append("published_at_precision")
        is_backfill = bool(item.published_at and item.first_seen_at and item.first_seen_at - item.published_at >= timedelta(days=14))
        if item.is_backfill != is_backfill:
            item.is_backfill = is_backfill
            updates.append("is_backfill")
        if updates:
            item.save(update_fields=updates)


class Migration(migrations.Migration):
    dependencies = [
        ("news", "0004_userfranchisefavorite_newsitem_canonical_url_hash_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
