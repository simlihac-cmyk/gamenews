from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand

from news.models import NewsItem
from news.services.quality import is_generic_summary
from news.services.summary_batch import (
    DEFAULT_MIN_RAW_CHARS,
    build_summary_batch_prompt,
    should_export_for_summary,
    summary_export_rejection_reasons,
)


class Command(BaseCommand):
    help = "Export a paste-ready ChatGPT/Gemini prompt for Korean summary generation."

    def add_arguments(self, parser):
        parser.add_argument("--item", type=int, action="append", default=None, help="Export a specific NewsItem ID. Repeatable.")
        parser.add_argument("--limit", type=int, default=20, help="Maximum number of items to export.")
        parser.add_argument("--force", action="store_true", help="Include items that already have useful summaries.")
        parser.add_argument("--output", default="", help="Optional output path. Prints to stdout when omitted.")
        parser.add_argument("--target", choices=["chatgpt", "gemini", "generic"], default="generic", help="Prompt target wording.")
        parser.add_argument("--max-source-chars", type=int, default=1800, help="Maximum raw excerpt characters per item.")
        parser.add_argument("--min-raw-chars", type=int, default=DEFAULT_MIN_RAW_CHARS, help="Skip items with shorter extracted text.")
        parser.add_argument("--include-low-quality", action="store_true", help="Include hub/short/low-confidence items in the export.")
        parser.add_argument("--show-skips", action="store_true", help="Print skipped item reasons to stderr.")

    def handle(self, *args, **options):
        qs = NewsItem.objects.select_related("raw_item", "source").filter(is_archived=False)
        if options["item"]:
            qs = qs.filter(pk__in=options["item"])
        qs = qs.order_by("-importance_score", "-published_at", "-created_at")

        selected: list[NewsItem] = []
        skipped_quality: dict[str, int] = {}
        skipped_existing = 0
        limit = options["limit"]
        for item in qs.iterator():
            if item.summary_ko and not options["force"]:
                if not is_generic_summary(item.summary_ko):
                    skipped_existing += 1
                    continue
            reasons = summary_export_rejection_reasons(item, min_raw_chars=options["min_raw_chars"])
            if reasons and not options["include_low_quality"]:
                for reason in reasons:
                    skipped_quality[reason] = skipped_quality.get(reason, 0) + 1
                if options["show_skips"]:
                    self.stderr.write(f"Skipped {item.pk}: {', '.join(reasons)} | {item.title}")
                continue
            if not should_export_for_summary(
                item,
                force=options["force"],
                include_low_quality=options["include_low_quality"],
                min_raw_chars=options["min_raw_chars"],
            ):
                continue
            selected.append(item)
            if limit and len(selected) >= limit:
                break

        if not selected:
            self.stderr.write("No items need summary export.")
            if skipped_quality:
                self.stderr.write(f"Skipped low-quality items: {_format_counts(skipped_quality)}")
            return

        prompt = build_summary_batch_prompt(
            selected,
            target=options["target"],
            max_source_chars=options["max_source_chars"],
            min_raw_chars=options["min_raw_chars"],
        )
        if skipped_quality:
            self.stderr.write(f"Exported {len(selected)} item(s); skipped low-quality items: {_format_counts(skipped_quality)}")
        elif skipped_existing:
            self.stderr.write(f"Exported {len(selected)} item(s); skipped existing summaries: {skipped_existing}")
        if options["output"]:
            path = Path(options["output"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(prompt, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Exported {len(selected)} item(s) to {path}"))
            return
        self.stdout.write(prompt)


def _format_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{reason}={count}" for reason, count in sorted(counts.items()))
