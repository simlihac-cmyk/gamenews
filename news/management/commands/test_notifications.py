from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from news.models import NotificationChannel
from news.services.notifier import send_test_notification


class Command(BaseCommand):
    help = "Send a small test notification to configured channels."

    def add_arguments(self, parser):
        parser.add_argument(
            "--channel",
            choices=["all", NotificationChannel.NTFY, NotificationChannel.DISCORD],
            default="all",
            help="Notification channel to test.",
        )
        parser.add_argument("--respect-enabled", action="store_true", help="Honor NOTIFICATIONS_ENABLED=false.")

    def handle(self, *args, **options):
        results = send_test_notification(channel=options["channel"], force=not options["respect_enabled"])
        failed = False
        for result in results:
            self.stdout.write(f"{result.channel}: {result.status} {result.error}".rstrip())
            failed = failed or result.status == "failed"
        if failed:
            raise CommandError("One or more test notifications failed.")
