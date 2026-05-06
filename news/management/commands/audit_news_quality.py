from __future__ import annotations

import logging

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from news.models import RawItem
from news.services.quality import (
    assess_date_quality,
    clean_title,
    is_boilerplate_title,
    is_hub_url,
)
from news.services.text import normalize_whitespace

logger = logging.getLogger(__name__)

DEFAULT_QUARANTINE_PREFIXES = ("date_suspect", "hub_url", "empty_title", "boilerplate_title")
SOFT_ISSUE_PREFIXES = ("long_title", "read_more_in_title")


class Command(BaseCommand):
    help = "Audit collected news quality and optionally quarantine suspect raw/news items."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write rejection/date flags and archive linked NewsItem records.")
        parser.add_argument(
            "--apply-soft",
            action="store_true",
            help="Also quarantine soft cleanup findings such as long_title/read_more_in_title.",
        )
        parser.add_argument("--limit", type=int, default=None, help="Maximum raw items to scan.")
        parser.add_argument(
            "--reasons",
            default="",
            help="Comma-separated issue prefixes to show/apply, e.g. hub_url,date_suspect.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        allowed_reasons = _parse_reasons(options["reasons"])
        qs = RawItem.objects.select_related("source").order_by("-collected_at", "-id")
        if options["limit"]:
            qs = qs[: options["limit"]]

        findings: list[tuple[RawItem, list[str]]] = []
        now = timezone.now()
        for raw in qs:
            issues = quality_issues_for_raw(raw, now=now)
            if allowed_reasons:
                issues = [issue for issue in issues if _matches_prefix(issue, allowed_reasons)]
            if issues:
                findings.append((raw, issues))

        for raw, issues in findings:
            self.stdout.write(f"{raw.pk}: {', '.join(issues)} | {raw.title} | {raw.canonical_url or raw.url}")

        quarantine_prefixes = _quarantine_prefixes(apply_soft=options["apply_soft"], allowed_reasons=allowed_reasons)
        quarantine_findings = [(raw, issues) for raw, issues in findings if _has_quarantine_issue(issues, quarantine_prefixes)]
        soft_only_count = len(findings) - len(quarantine_findings)

        if not apply:
            self.stdout.write(
                f"{len(findings)} item(s) flagged; {len(quarantine_findings)} would be quarantined by default"
                f"{'; ' + str(soft_only_count) + ' soft cleanup-only item(s) would be left unchanged' if soft_only_count else ''}."
            )
            self.stdout.write("Use --apply to quarantine default critical issues, or --apply --apply-soft to include soft cleanup findings.")
            return

        updated = 0
        skipped_soft = 0
        with transaction.atomic():
            for raw, issues in findings:
                if not _has_quarantine_issue(issues, quarantine_prefixes):
                    skipped_soft += 1
                    continue
                reason = _primary_reason(issues)
                date_quality = assess_date_quality(raw.published_at, now=now)
                raw.rejection_reason = raw.rejection_reason or reason
                raw.extraction_confidence = 0
                raw.date_confidence = date_quality.confidence
                raw.is_date_suspect = date_quality.is_suspect
                raw.date_suspect_reason = date_quality.reason
                raw.save(
                    update_fields=[
                        "rejection_reason",
                        "extraction_confidence",
                        "date_confidence",
                        "is_date_suspect",
                        "date_suspect_reason",
                    ]
                )
                if hasattr(raw, "news_item"):
                    item = raw.news_item
                    item.is_archived = True
                    item.extraction_confidence = 0
                    item.date_confidence = raw.date_confidence
                    item.is_date_suspect = raw.is_date_suspect
                    item.date_suspect_reason = raw.date_suspect_reason
                    item.save(
                        update_fields=[
                            "is_archived",
                            "extraction_confidence",
                            "date_confidence",
                            "is_date_suspect",
                            "date_suspect_reason",
                            "updated_at",
                        ]
                    )
                updated += 1
                logger.info("Quarantined raw item id=%s reason=%s title=%s", raw.pk, reason, raw.title)

        message = f"{updated} item(s) quarantined."
        if skipped_soft:
            message += f" {skipped_soft} soft cleanup-only item(s) left unchanged."
        self.stdout.write(self.style.SUCCESS(message))


def quality_issues_for_raw(raw: RawItem, *, now=None) -> list[str]:
    issues: list[str] = []
    title = normalize_whitespace(raw.title)
    cleaned = clean_title(title, raw.source.slug)
    date_quality = assess_date_quality(raw.published_at, now=now)
    if date_quality.is_suspect:
        issues.append(f"date_suspect:{date_quality.reason}")
    if is_hub_url(raw.canonical_url or raw.url, raw.source):
        issues.append("hub_url")
    if not title:
        issues.append("empty_title")
    if len(cleaned) > 120:
        issues.append("long_title")
    if "read more" in title.casefold():
        issues.append("read_more_in_title")
    if is_boilerplate_title(title, raw.source) or is_boilerplate_title(cleaned, raw.source):
        issues.append("boilerplate_title")
    return _dedupe(issues)


def _primary_reason(issues: list[str]) -> str:
    for prefix, reason in [
        ("date_suspect", "date_suspect"),
        ("hub_url", "hub_url"),
        ("empty_title", "empty_title"),
        ("boilerplate_title", "boilerplate_title"),
        ("long_title", "long_title"),
        ("read_more_in_title", "unclean_title"),
    ]:
        if any(issue.startswith(prefix) for issue in issues):
            return reason
    return "quality_suspect"


def _parse_reasons(value: str) -> tuple[str, ...]:
    return tuple(reason.strip() for reason in value.split(",") if reason.strip())


def _quarantine_prefixes(*, apply_soft: bool, allowed_reasons: tuple[str, ...]) -> tuple[str, ...]:
    prefixes = [*DEFAULT_QUARANTINE_PREFIXES]
    if apply_soft:
        prefixes.extend(SOFT_ISSUE_PREFIXES)
    if allowed_reasons:
        prefixes = [prefix for prefix in prefixes if _matches_prefix(prefix, allowed_reasons)]
    return tuple(prefixes)


def _has_quarantine_issue(issues: list[str], prefixes: tuple[str, ...]) -> bool:
    return any(_matches_prefix(issue, prefixes) for issue in issues)


def _matches_prefix(issue: str, prefixes: tuple[str, ...]) -> bool:
    return any(issue == prefix or issue.startswith(f"{prefix}:") for prefix in prefixes)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
