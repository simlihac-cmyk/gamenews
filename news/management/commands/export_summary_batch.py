from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand

from news.models import NewsItem
from news.services.summary_batch import build_summary_batch_prompt, should_export_for_summary


class Command(BaseCommand):
    help = "Export a paste-ready ChatGPT/Gemini prompt for Korean summary generation."

    def add_arguments(self, parser):
        parser.add_argument("--item", type=int, action="append", default=None, help="Export a specific NewsItem ID. Repeatable.")
        parser.add_argument("--limit", type=int, default=20, help="Maximum number of items to export.")
        parser.add_argument("--force", action="store_true", help="Include items that already have useful summaries.")
        parser.add_argument("--output", default="", help="Optional output path. Prints to stdout when omitted.")
        parser.add_argument("--target", choices=["chatgpt", "gemini", "generic"], default="generic", help="Prompt target wording.")
        parser.add_argument("--max-source-chars", type=int, default=1800, help="Maximum raw excerpt characters per item.")

    def handle(self, *args, **options):
        qs = NewsItem.objects.select_related("raw_item", "source").filter(is_archived=False)
        if options["item"]:
            qs = qs.filter(pk__in=options["item"])
        qs = qs.order_by("-importance_score", "-published_at", "-created_at")

        selected: list[NewsItem] = []
        limit = options["limit"]
        for item in qs.iterator():
            if not should_export_for_summary(item, force=options["force"]):
                continue
            selected.append(item)
            if limit and len(selected) >= limit:
                break

        if not selected:
            self.stdout.write("No items need summary export.")
            return

        prompt = build_summary_batch_prompt(
            selected,
            target=options["target"],
            max_source_chars=options["max_source_chars"],
        )
        if options["output"]:
            path = Path(options["output"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(prompt, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Exported {len(selected)} item(s) to {path}"))
            return
        self.stdout.write(prompt)
