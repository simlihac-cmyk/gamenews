from __future__ import annotations

from django.core.management.base import BaseCommand

from news.models import Issue
from news.services.issues import issue_review_metrics, refresh_issue_review_status


class Command(BaseCommand):
    help = "Mark suspect mixed-topic issues as review_required. Defaults to dry-run."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write review_required/review_reasons.")
        parser.add_argument("--dry-run", action="store_true", help="Show changes without saving. This is the default.")
        parser.add_argument("--issue-id", type=int, default=None, help="Limit to one issue ID.")
        parser.add_argument("--min-items", type=int, default=1, help="Only scan issues with at least this many linked items.")

    def handle(self, *args, **options):
        apply = options["apply"]
        qs = Issue.objects.prefetch_related("news_links__news_item__franchise_links").order_by("-last_updated_at")
        if options["issue_id"]:
            qs = qs.filter(pk=options["issue_id"])
        min_items = max(1, options["min_items"])

        changed = 0
        scanned = 0
        for issue in qs:
            item_count = issue.news_links.count()
            if item_count < min_items:
                continue
            scanned += 1
            review_required, reasons = refresh_issue_review_status(issue, save=False)
            would_change = issue.review_required != review_required or issue.review_reasons != reasons
            if would_change:
                changed += 1
            if review_required or would_change:
                metrics = issue_review_metrics(issue)
                prefix = "UPDATE" if apply and would_change else "DRY-RUN"
                self.stdout.write(
                    f"{prefix} issue #{issue.pk}: review_required={review_required} "
                    f"items={metrics.item_count} sources={metrics.source_count} "
                    f"primary_game_types={metrics.primary_franchise_count} "
                    f"avg_title_similarity={metrics.average_title_similarity:.2f} "
                    f"reasons={', '.join(reasons) or '-'} suggested_action=mark_review_required | {issue.title}"
                )
            if apply and would_change:
                issue.review_required = review_required
                issue.review_reasons = reasons
                issue.save(update_fields=["review_required", "review_reasons", "updated_at"])

        mode = "applied" if apply else "dry-run"
        self.stdout.write(self.style.SUCCESS(f"{mode}: scanned={scanned}, changed={changed}."))
