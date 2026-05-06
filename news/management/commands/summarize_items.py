from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from news.models import NewsItem
from news.services.classifier import ClassificationResult
from news.services.quality import is_generic_summary
from news.services.summarizer import summarize_item

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Generate or refresh Korean summaries for collected news items."

    def add_arguments(self, parser):
        parser.add_argument("--item", type=int, default=None, help="Summarize a single NewsItem ID.")
        parser.add_argument("--limit", type=int, default=None, help="Maximum number of items to summarize.")
        parser.add_argument("--force", action="store_true", help="Regenerate summaries even when a useful one already exists.")
        parser.add_argument(
            "--provider",
            choices=["rules", "openai", "auto"],
            default=None,
            help="Summary provider. Defaults to SUMMARY_PROVIDER.",
        )

    def handle(self, *args, **options):
        qs = NewsItem.objects.select_related("raw_item", "source").order_by("-created_at")
        if options["item"]:
            qs = qs.filter(pk=options["item"])
        else:
            qs = qs.filter(is_archived=False)
        if options["limit"]:
            qs = qs[: options["limit"]]

        updated = 0
        skipped = 0
        failed = 0
        for item in qs:
            if not options["force"] and item.summary_ko and not is_generic_summary(item.summary_ko):
                skipped += 1
                continue
            try:
                summary = summarize_item(
                    source=item.source,
                    title=item.title,
                    raw_text=item.raw_item.raw_text or item.summary_original,
                    classification=_classification_for(item),
                    provider=options["provider"],
                )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.exception("Failed to summarize item %s", item.pk)
                self.stderr.write(self.style.WARNING(f"Failed item {item.pk}: {exc}"))
                continue
            item.summary_ko = summary
            item.save(update_fields=["summary_ko", "updated_at"])
            updated += 1
            self.stdout.write(f"Updated {item.pk}: {item.title}")

        message = f"Summaries updated={updated}, skipped={skipped}, failed={failed}."
        if failed:
            self.stdout.write(self.style.WARNING(message))
        else:
            self.stdout.write(self.style.SUCCESS(message))


def _classification_for(item: NewsItem) -> ClassificationResult:
    return ClassificationResult(
        trust_label=item.trust_label,
        category=item.category,
        tags=list(item.detected_tags or []),
        confidence_score=item.confidence_score,
        trust_reasons=list(item.trust_reasons or []),
    )
