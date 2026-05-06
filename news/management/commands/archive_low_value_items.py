from __future__ import annotations

from datetime import timedelta
from io import StringIO

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from news.models import NewsItem


class Command(BaseCommand):
    help = "Archive old low-importance NewsItems."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=45, help="Archive items older than this many days.")
        parser.add_argument("--max-importance", type=int, default=25, help="Archive items at or below this importance.")
        parser.add_argument("--include-unread", action="store_true", help="Also archive unread items.")
        parser.add_argument("--dry-run", action="store_true", help="Show matching items without saving.")

    def handle(self, *args, **options):
        days = max(1, options["days"])
        max_importance = max(0, min(100, options["max_importance"]))
        cutoff = timezone.now() - timedelta(days=days)

        items = NewsItem.objects.filter(
            is_archived=False,
            is_bookmarked=False,
            importance_score__lte=max_importance,
        ).filter(
            Q(first_seen_at__lt=cutoff) | Q(published_at__lt=cutoff)
        ).order_by("importance_score", "first_seen_at")
        if not options["include_unread"]:
            items = items.filter(is_read=True)

        count = items.count()
        if options["dry_run"]:
            self.stdout.write(f"{count} item(s) would be archived.")
            buffer = StringIO()
            for item in items.select_related("source")[:20]:
                buffer.write(f"- #{item.pk} [{item.importance_score}] {item.title} ({item.source.name})\n")
            self.stdout.write(buffer.getvalue().rstrip())
            return

        updated = items.update(is_archived=True, updated_at=timezone.now())
        self.stdout.write(self.style.SUCCESS(f"{updated} item(s) archived."))
