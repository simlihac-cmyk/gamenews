from __future__ import annotations

import sys
from pathlib import Path

from django.core.management.base import BaseCommand

from news.models import NewsItem
from news.services.summary_batch import parse_summary_batch_response, should_export_for_summary, token_matches


class Command(BaseCommand):
    help = "Import Korean summaries generated from an exported ChatGPT/Gemini summary batch."

    def add_arguments(self, parser):
        parser.add_argument("--input", required=True, help="Path to the JSON response file, or '-' to read stdin.")
        parser.add_argument("--dry-run", action="store_true", help="Validate without updating NewsItem.summary_ko.")
        parser.add_argument("--force", action="store_true", help="Overwrite useful existing summaries.")

    def handle(self, *args, **options):
        text = sys.stdin.read() if options["input"] == "-" else Path(options["input"]).read_text(encoding="utf-8")
        summaries = parse_summary_batch_response(text)

        updated = 0
        skipped = 0
        failed = 0
        for summary in summaries:
            try:
                item = NewsItem.objects.get(pk=summary.item_id)
            except NewsItem.DoesNotExist:
                failed += 1
                self.stderr.write(self.style.WARNING(f"Missing item {summary.item_id}; skipped."))
                continue
            if not token_matches(item, summary.token):
                failed += 1
                self.stderr.write(self.style.WARNING(f"Token mismatch for item {summary.item_id}; skipped."))
                continue
            if not should_export_for_summary(item, force=options["force"]):
                skipped += 1
                self.stdout.write(f"Skipped {item.pk}: useful summary already exists.")
                continue
            if options["dry_run"]:
                updated += 1
                self.stdout.write(f"Would update {item.pk}: {item.title}")
                continue
            item.summary_ko = summary.summary_ko
            item.save(update_fields=["summary_ko", "updated_at"])
            updated += 1
            self.stdout.write(f"Updated {item.pk}: {item.title}")

        action = "would update" if options["dry_run"] else "updated"
        message = f"Summary import {action}={updated}, skipped={skipped}, failed={failed}."
        if failed:
            self.stdout.write(self.style.WARNING(message))
        else:
            self.stdout.write(self.style.SUCCESS(message))
