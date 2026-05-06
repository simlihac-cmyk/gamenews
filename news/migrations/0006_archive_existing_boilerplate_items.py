from __future__ import annotations

import re
from html import unescape

from django.db import migrations


EXACT = {
    "skip to main content",
    "read more",
    "shop all",
    "characters hub",
    "smart device games",
    "monthly highlights",
    "my nintendo store shop all",
    "nintendo switch - oled model",
    "nintendo switch – oled model",
    "which nintendo switch is right for you",
}
CONTAINS = {"shop all", "characters hub", "smart device games"}


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value or "")).strip().casefold()


def rejection_reason(title: str) -> str:
    value = clean(title)
    if not value:
        return "empty_title"
    ascii_dash = value.replace("–", "-").replace("—", "-")
    if value in EXACT or ascii_dash in EXACT or value.startswith("skip to"):
        return "boilerplate_title"
    if len(value) < 10:
        return "too_short_title"
    if any(marker in value for marker in CONTAINS):
        return "boilerplate_title"
    return ""


def forwards(apps, schema_editor):
    RawItem = apps.get_model("news", "RawItem")
    for raw in RawItem.objects.filter(rejection_reason="").iterator():
        reason = rejection_reason(raw.title)
        if not reason:
            continue
        raw.rejection_reason = reason
        raw.extraction_confidence = 0
        raw.save(update_fields=["rejection_reason", "extraction_confidence"])
        if hasattr(raw, "news_item"):
            raw.news_item.is_archived = True
            raw.news_item.extraction_confidence = 0
            raw.news_item.save(update_fields=["is_archived", "extraction_confidence", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("news", "0005_backfill_quality_fields"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
