from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from news.models import NewsItem
from news.services.collectors import recalculate_news_item

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Recalculate classification, summaries, importance, franchises, and issue links for existing items."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None, help="Maximum number of items to recalculate.")
        parser.add_argument("--item", type=int, default=None, help="Recalculate a single NewsItem ID.")

    def handle(self, *args, **options):
        qs = NewsItem.objects.select_related("raw_item", "source").order_by("-created_at")
        if options["item"]:
            qs = qs.filter(pk=options["item"])
        if options["limit"]:
            qs = qs[: options["limit"]]

        count = 0
        for news_item in qs:
            try:
                updated = recalculate_news_item(news_item)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to recalculate item %s", news_item.pk)
                self.stderr.write(self.style.WARNING(f"Failed item {news_item.pk}: {exc}"))
                continue
            count += 1
            self.stdout.write(f"Updated {updated.pk}: {updated.title} ({updated.importance_score})")

        self.stdout.write(self.style.SUCCESS(f"Recalculated {count} items."))
