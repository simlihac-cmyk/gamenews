from __future__ import annotations

from django.core.management.base import BaseCommand

from news.models import NewsItem
from news.services.classifier import classify_item
from news.services.importance import calculate_importance_with_reasons, calculate_nintendo_relevance, score_reason
from news.services.quality import classify_content_type, date_quality_update_fields, is_backfill_item, title_quality
from news.services.text import normalize_title


class Command(BaseCommand):
    help = "Recalculate score/reason/date/title quality metadata. Defaults to dry-run."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write recalculated fields.")
        parser.add_argument("--dry-run", action="store_true", help="Show changes without saving. This is the default.")
        parser.add_argument("--item", type=int, default=None, help="Limit to one NewsItem ID.")
        parser.add_argument("--source", type=str, default="", help="Limit to one Source slug.")
        parser.add_argument("--only-missing-reasons", action="store_true", help="Only update items whose score reasons are missing or placeholders.")
        parser.add_argument("--limit", type=int, default=None, help="Maximum items to scan.")

    def handle(self, *args, **options):
        qs = NewsItem.objects.select_related("raw_item", "source").prefetch_related("franchise_links__franchise").order_by("-created_at")
        if options["item"]:
            qs = qs.filter(pk=options["item"])
        if options["source"]:
            qs = qs.filter(source__slug=options["source"])
        if options["limit"]:
            qs = qs[: options["limit"]]

        scanned = 0
        changed = 0
        for item in qs:
            scanned += 1
            if options["only_missing_reasons"] and not _needs_reason_recalc(item):
                continue
            updates = recalculated_fields(item)
            if options["only_missing_reasons"]:
                updates = {
                    "importance_reasons": updates["importance_reasons"],
                    "trust_reasons": updates["trust_reasons"],
                }
            diff = {field: value for field, value in updates.items() if getattr(item, field) != value}
            if not diff:
                continue
            changed += 1
            display_title = updates.get("title", item.title)
            self.stdout.write(
                f"{'UPDATE' if options['apply'] else 'DRY-RUN'} item #{item.pk}: "
                f"importance {item.importance_score}->{updates.get('importance_score', item.importance_score)} "
                f"relevance {item.nintendo_relevance_score}->{updates.get('nintendo_relevance_score', item.nintendo_relevance_score)} | {display_title}"
            )
            if options["apply"]:
                for field, value in diff.items():
                    setattr(item, field, value)
                item.save(update_fields=[*diff.keys(), "updated_at"])

        mode = "applied" if options["apply"] else "dry-run"
        self.stdout.write(self.style.SUCCESS(f"{mode}: scanned={scanned}, changed={changed}."))


def recalculated_fields(item: NewsItem) -> dict[str, object]:
    raw = item.raw_item
    title_info = title_quality(raw.title, item.source.slug)
    title = title_info.cleaned or raw.title
    classification = classify_item(item.source, title, raw.raw_text)
    date_quality = date_quality_update_fields(raw.published_at)
    content_type = classify_content_type(
        title=title,
        url=raw.canonical_url or raw.url,
        raw_text=raw.raw_text,
        source=item.source,
    )
    franchises = [link.franchise for link in item.franchise_links.all() if link.is_primary]
    relevance = calculate_nintendo_relevance(
        source=item.source,
        title=title,
        raw_text=raw.raw_text,
        tags=classification.tags,
        franchises=franchises,
    )
    importance, importance_reasons = calculate_importance_with_reasons(
        source=item.source,
        title=title,
        raw_text=raw.raw_text,
        tags=classification.tags,
        franchises=franchises,
        published_at=raw.published_at,
        first_seen_at=raw.first_seen_at,
        content_type=content_type,
        title_suspect=title_info.is_suspect,
        date_confidence=str(date_quality["date_confidence"]),
        nintendo_relevance_score=relevance,
    )
    if importance > 0 and not importance_reasons:
        importance_reasons = [score_reason("fallback_importance_reason", "자동 중요도 계산 기준")]
    trust_reasons = classification.trust_reasons or [score_reason("fallback_trust_reason", "출처 기준 신뢰도 계산")]
    return {
        "title": title,
        "normalized_title": normalize_title(title),
        "trust_label": classification.trust_label,
        "category": classification.category,
        "detected_tags": classification.tags,
        "confidence_score": classification.confidence_score,
        "importance_score": importance,
        "importance_reasons": importance_reasons,
        "trust_reasons": trust_reasons,
        "title_suspect": title_info.is_suspect,
        "title_suspect_reason": title_info.reason,
        "content_type": content_type,
        "nintendo_relevance_score": relevance,
        "published_at": raw.published_at,
        "published_at_precision": raw.published_at_precision,
        "is_backfill": is_backfill_item(raw.published_at, raw.first_seen_at),
        "date_confidence": date_quality["date_confidence"],
        "is_date_suspect": date_quality["is_date_suspect"],
        "date_suspect_reason": date_quality["date_suspect_reason"],
    }


def _needs_reason_recalc(item: NewsItem) -> bool:
    return _reason_missing_or_placeholder(item.importance_reasons) or _reason_missing_or_placeholder(item.trust_reasons)


def _reason_missing_or_placeholder(reasons) -> bool:
    if not reasons:
        return True
    for reason in reasons:
        if isinstance(reason, dict):
            value = str(reason.get("label") or reason.get("code") or "")
        else:
            value = str(reason)
        if "재계산 필요" in value:
            return True
    return False
