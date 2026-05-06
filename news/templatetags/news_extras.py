from django import template

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
