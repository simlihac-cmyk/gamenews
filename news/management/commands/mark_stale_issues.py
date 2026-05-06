from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from news.models import Issue, IssueStatus


class Command(BaseCommand):
    help = "Mark old rumor/developing issues as stale."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30, help="Mark issues stale after this many days without updates.")
        parser.add_argument("--dry-run", action="store_true", help="Show what would be marked stale without saving.")

    def handle(self, *args, **options):
        days = max(1, options["days"])
        cutoff = timezone.now() - timedelta(days=days)
        issues = Issue.objects.filter(
            status__in=[IssueStatus.RUMOR, IssueStatus.DEVELOPING],
            last_updated_at__lt=cutoff,
        ).order_by("last_updated_at")

        count = issues.count()
        if options["dry_run"]:
            self.stdout.write(f"{count} issue(s) would be marked stale.")
            for issue in issues[:20]:
                self.stdout.write(f"- #{issue.pk} {issue.title} ({issue.last_updated_at:%Y-%m-%d})")
            return

        updated = issues.update(status=IssueStatus.STALE, updated_at=timezone.now())
        self.stdout.write(self.style.SUCCESS(f"{updated} issue(s) marked stale."))
