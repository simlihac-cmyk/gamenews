from __future__ import annotations

from django.core.management.base import BaseCommand

from news.models import Issue
from news.services.issues import rebuild_issue_title


class Command(BaseCommand):
    help = "Rebuild issue titles from clean non-suspect linked item titles. Defaults to dry-run."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write rebuilt titles.")
        parser.add_argument("--dry-run", action="store_true", help="Show changes without saving. This is the default.")
        parser.add_argument("--issue-id", type=int, default=None, help="Limit to one issue ID.")
        parser.add_argument("--limit", type=int, default=None, help="Maximum issues to scan.")

    def handle(self, *args, **options):
        qs = Issue.objects.prefetch_related("news_links__news_item").order_by("-last_updated_at")
        if options["issue_id"]:
            qs = qs.filter(pk=options["issue_id"])
        if options["limit"]:
            qs = qs[: options["limit"]]

        changed = 0
        scanned = 0
        for issue in qs:
            scanned += 1
            title, would_change = rebuild_issue_title(issue, save=False)
            if not would_change:
                continue
            changed += 1
            self.stdout.write(f"{'UPDATE' if options['apply'] else 'DRY-RUN'} issue #{issue.pk}: {issue.title} -> {title}")
            if options["apply"]:
                rebuild_issue_title(issue, save=True)

        mode = "applied" if options["apply"] else "dry-run"
        self.stdout.write(self.style.SUCCESS(f"{mode}: scanned={scanned}, changed={changed}."))
