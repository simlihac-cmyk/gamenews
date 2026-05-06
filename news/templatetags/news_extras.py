from django import template

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
    "debunk": "반박",
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
def show_summary(value: str) -> bool:
    return bool(value and not is_generic_summary(value))


@register.simple_tag
def item_badges(item):
    badges: list[dict[str, str]] = []

    def add(label: str, css_class: str = "") -> None:
        key = label.casefold()
        if not label or any(existing["label"].casefold() == key for existing in badges):
            return
        badges.append({"label": label, "class": css_class})

    if item.importance_score >= 80:
        add("중요", "important")
    add("공식 출처" if item.trust_label == "official" else item.trust_label_ko, item.trust_label)
    if item.category not in {"official", "general"}:
        add(item.category_ko, category_class(item.category))
    for tag in item.detected_tags:
        if tag in {"direct", "release_date", "trailer", "switch2"}:
            add(tag_label(tag), tag_class(tag))
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
