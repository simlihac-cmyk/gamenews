from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from news.models import NewsItem, Notification, NotificationChannel, NotificationStatus

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class NotificationTarget:
    channel: str
    configured: bool


def notify_if_needed(news_item: NewsItem, *, force: bool = False) -> list[Notification]:
    """Send configured notifications for an important item.

    A successful notification is unique per item/channel. Failed attempts are
    recorded and can be retried on a later run.
    """
    if not settings.NOTIFICATIONS_ENABLED and not force:
        return [_skip(news_item, NotificationChannel.NONE, "notifications disabled")]

    threshold = settings.NOTIFICATION_MIN_IMPORTANCE
    if news_item.importance_score < threshold and not force:
        return [_skip(news_item, NotificationChannel.NONE, f"importance below {threshold}")]

    targets = configured_targets()
    if not any(target.configured for target in targets):
        return [_skip(news_item, NotificationChannel.NONE, "no notification channel configured")]

    notifications: list[Notification] = []
    for target in targets:
        if not target.configured:
            continue
        if target.channel == NotificationChannel.NTFY:
            notifications.append(send_ntfy(news_item))
        elif target.channel == NotificationChannel.DISCORD:
            notifications.append(send_discord(news_item))
    return notifications


def configured_targets() -> list[NotificationTarget]:
    return [
        NotificationTarget(NotificationChannel.NTFY, bool(settings.NTFY_TOPIC)),
        NotificationTarget(NotificationChannel.DISCORD, bool(settings.DISCORD_WEBHOOK_URL)),
    ]


def send_ntfy(news_item: NewsItem) -> Notification:
    existing = _already_sent(news_item, NotificationChannel.NTFY)
    if existing:
        logger.info("Skipping duplicate ntfy notification item=%s", news_item.pk)
        return existing

    notification = _create_attempt(news_item, NotificationChannel.NTFY)
    url = f"{settings.NTFY_SERVER.rstrip('/')}/{settings.NTFY_TOPIC.lstrip('/')}"
    message = format_korean_message(news_item)
    headers = {
        "Title": _header_value(f"Nintendo Watch: {news_item.title}", max_length=120),
        "Tags": "video_game",
        "Priority": "5" if news_item.importance_score >= 90 else "4",
    }
    try:
        response = httpx.post(
            url,
            content=message.encode("utf-8"),
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - notification failure should not stop collection.
        _mark_failed(notification, exc)
    else:
        _mark_sent(notification)
    return notification


def send_discord(news_item: NewsItem) -> Notification:
    existing = _already_sent(news_item, NotificationChannel.DISCORD)
    if existing:
        logger.info("Skipping duplicate Discord notification item=%s", news_item.pk)
        return existing

    notification = _create_attempt(news_item, NotificationChannel.DISCORD)
    payload = {
        "username": "Nintendo Watch",
        "embeds": [
            {
                "title": news_item.title[:256],
                "url": news_item.url,
                "description": news_item.summary_ko[:3900],
                "color": _discord_color(news_item),
                "fields": [
                    {"name": "신뢰도", "value": news_item.trust_label_ko, "inline": True},
                    {"name": "카테고리", "value": news_item.category_ko, "inline": True},
                    {"name": "중요도", "value": str(news_item.importance_score), "inline": True},
                    {"name": "출처", "value": news_item.source.name[:1024], "inline": True},
                    {"name": "원문", "value": news_item.url[:1024], "inline": False},
                ],
                "footer": {"text": "Nintendo Watch"},
            }
        ],
    }
    try:
        response = httpx.post(settings.DISCORD_WEBHOOK_URL, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        _mark_failed(notification, exc)
    else:
        _mark_sent(notification)
    return notification


def format_korean_message(news_item: NewsItem) -> str:
    return (
        "Nintendo Watch\n"
        f"제목: {news_item.title}\n"
        f"신뢰도: {news_item.trust_label_ko}\n"
        f"카테고리: {news_item.category_ko}\n"
        f"중요도: {news_item.importance_score}\n\n"
        f"요약:\n{news_item.summary_ko}\n\n"
        f"원문:\n{news_item.url}"
    )


def _create_attempt(news_item: NewsItem, channel: str) -> Notification:
    return Notification.objects.create(
        news_item=news_item,
        channel=channel,
        status=NotificationStatus.PENDING,
    )


def _already_sent(news_item: NewsItem, channel: str) -> Notification | None:
    return news_item.notifications.filter(channel=channel, status=NotificationStatus.SENT).first()


def _skip(news_item: NewsItem, channel: str, reason: str) -> Notification:
    return Notification.objects.create(
        news_item=news_item,
        channel=channel,
        status=NotificationStatus.SKIPPED,
        error=reason,
    )


def _mark_sent(notification: Notification) -> None:
    try:
        with transaction.atomic():
            notification.status = NotificationStatus.SENT
            notification.sent_at = timezone.now()
            notification.error = ""
            notification.save(update_fields=["status", "sent_at", "error"])
    except IntegrityError:
        logger.info(
            "Notification was already marked sent for item=%s channel=%s",
            notification.news_item_id,
            notification.channel,
        )
        notification.status = NotificationStatus.SKIPPED
        notification.error = "duplicate sent notification"
        notification.save(update_fields=["status", "error"])


def _mark_failed(notification: Notification, exc: Exception) -> None:
    logger.warning(
        "Notification failed item=%s channel=%s error=%s",
        notification.news_item_id,
        notification.channel,
        exc,
    )
    notification.status = NotificationStatus.FAILED
    notification.error = str(exc)[:2000]
    notification.save(update_fields=["status", "error"])


def _header_value(value: str, *, max_length: int) -> str:
    clean = " ".join(value.split())
    return clean[:max_length]


def _discord_color(news_item: NewsItem) -> int:
    if news_item.trust_label == "official":
        return 0x208A46
    if news_item.trust_label == "reported":
        return 0x1F6FEB
    if news_item.trust_label == "rumor":
        return 0xA15C00
    return 0x687385
