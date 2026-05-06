from __future__ import annotations

from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


def _date_suspect_reason(value, *, now):
    if value is None:
        return ""
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone=timezone.get_current_timezone())
    if value > now + timedelta(hours=24):
        return "future_more_than_24h"
    if value.year < 1996:
        return "before_1996"
    if value.year > now.year + 1:
        return "year_too_far_future"
    return ""


def forwards(apps, schema_editor):
    RawItem = apps.get_model("news", "RawItem")
    NewsItem = apps.get_model("news", "NewsItem")
    now = timezone.now()

    for raw in RawItem.objects.all().iterator():
        reason = _date_suspect_reason(raw.published_at, now=now)
        if not reason:
            continue
        raw.is_date_suspect = True
        raw.date_confidence = "low"
        raw.date_suspect_reason = reason
        raw.rejection_reason = raw.rejection_reason or "date_suspect"
        raw.save(update_fields=["is_date_suspect", "date_confidence", "date_suspect_reason", "rejection_reason"])

    for item in NewsItem.objects.select_related("raw_item").all().iterator():
        raw_reason = getattr(item.raw_item, "date_suspect_reason", "")
        reason = raw_reason or _date_suspect_reason(item.published_at, now=now)
        if not reason:
            continue
        item.is_date_suspect = True
        item.date_confidence = "low"
        item.date_suspect_reason = reason
        item.is_archived = True
        item.save(update_fields=["is_date_suspect", "date_confidence", "date_suspect_reason", "is_archived", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("news", "0007_update_franchise_aliases"),
    ]

    operations = [
        migrations.AddField(
            model_name="rawitem",
            name="date_confidence",
            field=models.CharField(choices=[("high", "High"), ("medium", "Medium"), ("low", "Low")], default="high", max_length=16),
        ),
        migrations.AddField(
            model_name="rawitem",
            name="date_suspect_reason",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="rawitem",
            name="is_date_suspect",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="newsitem",
            name="date_confidence",
            field=models.CharField(choices=[("high", "High"), ("medium", "Medium"), ("low", "Low")], default="high", max_length=16),
        ),
        migrations.AddField(
            model_name="newsitem",
            name="date_suspect_reason",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="newsitem",
            name="entity_mentions",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="newsitem",
            name="is_date_suspect",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="newsitem",
            name="trust_reasons",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="newsitemfranchise",
            name="is_primary",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddIndex(
            model_name="rawitem",
            index=models.Index(fields=["is_date_suspect", "published_at"], name="news_rawite_is_date_256f11_idx"),
        ),
        migrations.AddIndex(
            model_name="newsitem",
            index=models.Index(fields=["is_date_suspect", "published_at"], name="news_newsit_is_date_6d0f23_idx"),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
