from __future__ import annotations

from collections.abc import Iterable

from news.models import Franchise, Source, TrustType

from .text import normalize_title


TRUST_POINTS = {
    TrustType.OFFICIAL: 50,
    TrustType.PRESS: 25,
    TrustType.RUMOR: 10,
    TrustType.UNKNOWN: 5,
}

KEYWORD_POINTS = [
    (["nintendo direct", "닌텐도 다이렉트", "ニンテンドーダイレクト"], 40),
    (["switch2", "switch 2", "nintendo switch 2", "차세대기"], 30),
    (["release date", "launch date", "발매일", "출시일", "発売日"], 25),
    (["new game", "신작", "announced", "발표"], 25),
    (["trailer", "트레일러", "pv"], 20),
    (["leak", "rumor", "rumour", "유출", "루머"], 10),
    (["sale", "세일", "할인", "discount"], 5),
]


def calculate_importance(
    *,
    source: Source,
    title: str,
    raw_text: str = "",
    tags: Iterable[str] | None = None,
    franchises: Iterable[Franchise] | None = None,
) -> int:
    score = source.base_score or TRUST_POINTS.get(source.trust_type, 5)
    normalized = normalize_title(f"{title} {raw_text}")
    raw_lower = f"{title} {raw_text}".lower()

    for keywords, points in KEYWORD_POINTS:
        if any(keyword.lower() in raw_lower or normalize_title(keyword) in normalized for keyword in keywords):
            score += points

    tag_set = set(tags or [])
    if "switch2" in tag_set:
        score += 30
    if "direct" in tag_set:
        score += 20

    franchise_list = list(franchises or [])
    if any(franchise.priority >= 80 for franchise in franchise_list):
        score += 20
    elif franchise_list:
        score += 10

    return max(0, min(100, score))

