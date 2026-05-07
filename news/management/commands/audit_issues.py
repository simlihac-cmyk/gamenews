from __future__ import annotations

from django.core.management.base import BaseCommand

from news.models import Issue
from news.services.issues import issue_review_metrics, issue_review_reasons


class Command(BaseCommand):
    help = "Audit suspect mixed-topic issues without changing data."

    def add_arguments(self, parser):
        parser.add_argument("--min-items", type=int, default=10, help="Only show issues with at least this many linked items unless another reason matches.")
        parser.add_argument("--issue-id", type=int, default=None, help="Audit one issue ID.")
        parser.add_argument("--dry-run", action="store_true", help="Accepted for safety; this command is read-only.")

    def handle(self, *args, **options):
        qs = Issue.objects.prefetch_related("news_links__news_item__franchise_links").order_by("-last_updated_at")
        if options["issue_id"]:
            qs = qs.filter(pk=options["issue_id"])

        findings = []
        min_items = max(1, options["min_items"])
        for issue in qs:
            reasons = issue_review_reasons(issue)
            metrics = issue_review_metrics(issue)
            if reasons or metrics.item_count >= min_items:
                findings.append((issue, metrics, reasons))

        for issue, metrics, reasons in findings:
            reason_text = ", ".join(reasons) if reasons else "min_items_only"
            suggested_action = "mark_review_required" if reasons else "inspect"
            self.stdout.write(
                "issue #{id}: title={title} | items={items} same_story={same_story} "
                "sources={sources} primary_game_types={primary_game_types} "
                "avg_title_similarity={similarity:.2f} review_required={review_required} "
                "reasons={reasons} suggested_action={action}".format(
                    id=issue.pk,
                    title=issue.title,
                    items=metrics.item_count,
                    same_story=metrics.same_story_count,
                    sources=metrics.source_count,
                    primary_game_types=metrics.primary_franchise_count,
                    similarity=metrics.average_title_similarity,
                    review_required=bool(reasons),
                    reasons=reason_text,
                    action=suggested_action,
                )
            )

        self.stdout.write(f"{len(findings)} issue(s) audited; dry-run/read-only.")
