from django import template
from django.utils import timezone

from news.models import PublishedAtPrecision, SourceType
from news.services.importance import reason_labels as extract_reason_labels
from news.services.quality import is_generic_summary

register = template.Library()


TAG_LABELS = {
    "direct": "Direct",
    "release_date": "발매일",
    "trailer": "트레일러",
    "new_game": "신작",
    "update": "업데이트",
    "sale": "세일",
    "rumor": "루머",
    "leak": "유출",
    "switch": "Switch",
    "switch2": "Switch 2",
}

TAG_CLASSES = {
    "direct": "tag-direct",
    "release_date": "tag-release-date",
    "trailer": "tag-trailer",
    "switch2": "tag-switch2",
    "rumor": "tag-rumor",
    "leak": "tag-leak",
}

CATEGORY_CLASSES = {
    "direct": "tag-direct",
    "release_date": "tag-release-date",
    "trailer": "tag-trailer",
    "rumor": "tag-rumor",
    "leak": "tag-leak",
    "official": "official",
}

RELATION_LABELS = {
    "same_story": "같은 이야기",
    "followup": "후속",
    "confirmation": "공식 확인",
    "official_confirmation": "공식 확인",
    "debunk": "반박",
    "contradicts": "반박/정정",
    "source_duplicate": "중복 출처",
    "related": "관련",
}


@register.filter
def tag_label(value: str) -> str:
    return TAG_LABELS.get(value, value)


@register.filter
def tag_class(value: str) -> str:
    return TAG_CLASSES.get(value, "")


@register.filter
def category_class(value: str) -> str:
    return CATEGORY_CLASSES.get(value, "")


@register.filter
def relation_label(value: str) -> str:
    return RELATION_LABELS.get(value, value)


@register.filter
def reason_labels(value) -> list[str]:
    labels = extract_reason_labels(value)
    return labels or ["점수 설명 준비 중"]


@register.filter
def show_summary(value: str) -> bool:
    return bool(value and not is_generic_summary(value))


@register.filter
def summary_blocks(value: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    for line in str(value or "").splitlines():
        clean = " ".join(line.split())
        if not clean:
            continue
        label, text = _split_summary_line(clean)
        blocks.append({"label": label, "text": text})
    if not blocks and value:
        blocks.append({"label": "", "text": " ".join(str(value).split())})
    return blocks


@register.filter
def summary_preview(value: str) -> list[dict[str, str]]:
    blocks = summary_blocks(value)
    priority = {"무슨 일?": 0, "왜 중요?": 1, "확인 상태": 2, "주의": 3}
    blocks.sort(key=lambda block: priority.get(block["label"], 9))
    return blocks[:2]


def _split_summary_line(value: str) -> tuple[str, str]:
    known_labels = ["무슨 일?", "왜 중요?", "확인 상태", "주의"]
    for label in known_labels:
        prefix = f"{label}:"
        if value.startswith(prefix):
            return label, value[len(prefix) :].strip()
    if ":" in value:
        label, text = value.split(":", 1)
        if 1 <= len(label) <= 12:
            return label.strip(), text.strip()
    return "", value


@register.simple_tag
def item_badges(item):
    badges: list[dict[str, str]] = []

    def add(label: str, css_class: str = "") -> None:
        css = " ".join(dict.fromkeys(str(css_class or "").split()))
        key = label.casefold()
        if not label or any(existing["label"].casefold() == key for existing in badges):
            return
        badges.append({"label": label, "class": css})

    issue_links = list(getattr(item, "issue_links", []).all()) if hasattr(getattr(item, "issue_links", None), "all") else []
    if any(getattr(link.issue, "review_required", False) for link in issue_links if getattr(link, "issue", None)):
        add("검토 필요", "state")
    if item.importance_score >= 80:
        add("중요", "important")
    add(item.trust_label_ko, item.trust_label)
    if item.category not in {"official", "general"}:
        add(item.category_ko, category_class(item.category))
    for tag in item.detected_tags:
        if tag in {"direct", "release_date", "trailer", "switch2"}:
            add(tag_label(tag), tag_class(tag))
    if getattr(item, "title_suspect", False):
        add("제목 확인 필요", "state")
    if getattr(item, "is_date_suspect", False):
        add("날짜 확인 필요", "state")
    if item.is_backfill:
        add("과거 기사 수집", "state")
    if not item.published_at:
        add("게시일 미상", "state")
    if item.is_bookmarked:
        add("북마크", "reported")
    if item.is_read:
        add("읽음", "state")
    return badges


@register.simple_tag
def published_status(item) -> str:
    if getattr(item, "is_date_suspect", False):
        reason = getattr(item, "date_suspect_reason", "")
        return f"게시: 확인 필요 · {reason}" if reason else "게시: 확인 필요"
    if not getattr(item, "published_at", None):
        return "게시: 미상 · 수집일 기준 정렬"
    published = timezone.localtime(item.published_at).strftime("%Y-%m-%d %H:%M")
    if getattr(item, "published_at_precision", "") == PublishedAtPrecision.DATE_ONLY:
        return f"게시: {published} KST · 날짜만 확인됨"
    if getattr(item, "date_confidence", "") == "medium":
        return f"게시: {published} KST · 추정 날짜"
    return f"게시: {published} KST"


@register.simple_tag
def source_attribution(item):
    metadata = getattr(getattr(item, "raw_item", None), "metadata", {}) or {}
    source = item.source
    original = metadata.get("original_source") or ""
    display = metadata.get("display_source") or source.name
    transfer = metadata.get("transfer_source") or ""
    collection = metadata.get("collection_source") or source.name
    rows: list[dict[str, str]] = []

    if source.trust_type == "official":
        rows.append({"label": "원출처", "value": original or source.name})
        rows.append({"label": "수집 출처", "value": collection})
        return rows

    rows.append({"label": "표시 출처", "value": display})
    if source.source_type == SourceType.REDDIT_RSS or source.trust_type == "rumor":
        rows.append({"label": "전달 출처", "value": transfer or "Reddit 게시물"})
        rows.append({"label": "원출처", "value": original or "원출처 확인 필요"})
    elif original:
        rows.append({"label": "원출처", "value": original})
    rows.append({"label": "수집 출처", "value": collection})
    return rows
