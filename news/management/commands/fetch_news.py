from __future__ import annotations

import logging

from django.core.management.base import BaseCommand, CommandError

from news.models import Source
from news.services.collectors import collect_source, fetch_enabled_sources, process_raw_item
from news.services.notifier import clear_source_failure_alert, notify_if_needed, notify_source_failure

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Fetch news from configured sources and create processed NewsItem records."

    def add_arguments(self, parser):
        parser.add_argument("--source", dest="source_slug", help="Fetch a single source by slug.")
        parser.add_argument("--limit", type=int, default=None, help="Maximum entries to fetch per source.")
        parser.add_argument("--notify", action="store_true", help="Run notification checks for newly created items.")
        parser.add_argument("--dry-run", action="store_true", help="Fetch and parse without writing records.")

    def handle(self, *args, **options):
        source_slug = options["source_slug"]
        limit = options["limit"]
        dry_run = options["dry_run"]
        notify = options["notify"]

        if source_slug:
            try:
                sources = [Source.objects.get(slug=source_slug)]
            except Source.DoesNotExist as exc:
                raise CommandError(f"Source not found: {source_slug}") from exc
        else:
            sources = list(fetch_enabled_sources())

        total_raw = 0
        total_news_created = 0
        total_news_existing = 0

        for source in sources:
            self.stdout.write(f"Fetching {source.slug}...")
            result = collect_source(source, limit=limit, dry_run=dry_run)
            if dry_run:
                self.stdout.write(
                    f"  dry-run items={len(result.dry_run_items)} skipped={result.skipped_count} "
                    f"errors={len(result.errors)} elapsed={result.elapsed_seconds:.2f}s"
                )
                for item in result.dry_run_items[:5]:
                    self.stdout.write(f"    - {item['title']} ({item['canonical_url']})")
                continue

            total_raw += result.created_count
            self.stdout.write(
                f"  found={result.found_count} raw created={result.created_count} "
                f"duplicates={result.duplicate_count} skipped={result.skipped_count} "
                f"errors={len(result.errors)} elapsed={result.elapsed_seconds:.2f}s"
            )
            if notify:
                if result.errors:
                    alert = notify_source_failure(source, "; ".join(result.errors))
                    self.stdout.write(f"  source alert {alert.channel}/{alert.status}")
                else:
                    clear_source_failure_alert(source)

            for raw_item in result.raw_items:
                try:
                    news_item, created = process_raw_item(raw_item)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Failed to process raw item %s", raw_item.pk)
                    self.stderr.write(self.style.WARNING(f"  failed raw item {raw_item.pk}: {exc}"))
                    continue
                if news_item is None:
                    self.stdout.write(f"    - rejected raw item {raw_item.pk}: {raw_item.rejection_reason}")
                    continue

                if created:
                    total_news_created += 1
                    self.stdout.write(
                        f"    + {news_item.title} [{news_item.trust_label}/{news_item.category}/"
                        f"{news_item.importance_score}]"
                    )
                    if notify:
                        notifications = notify_if_needed(news_item)
                        for notification in notifications:
                            self.stdout.write(
                                f"      notification {notification.channel}/{notification.status}"
                            )
                else:
                    total_news_existing += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Raw created={total_raw}, news created={total_news_created}, "
                f"news existing={total_news_existing}."
            )
        )
