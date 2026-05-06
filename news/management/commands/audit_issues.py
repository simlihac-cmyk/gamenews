from __future__ import annotations

from django.core.management.base import BaseCommand

from news.models import Issue
from news.services.issues import issue_review_reasons


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
            item_count = issue.news_links.count()
            if reasons or item_count >= min_items:
                findings.append((issue, item_count, reasons))

        for issue, item_count, reasons in findings:
            reason_text = ", ".join(reasons) if reasons else "min_items_only"
            self.stdout.write(f"issue #{issue.pk}: items={item_count} review_required={bool(reasons)} reasons={reason_text} | {issue.title}")

        self.stdout.write(f"{len(findings)} issue(s) audited; dry-run/read-only.")
