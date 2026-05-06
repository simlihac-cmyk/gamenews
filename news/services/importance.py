from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta

from news.models import DateConfidence, Franchise, NewsContentType, Source, TrustType

from .quality import is_boilerplate_title
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

STATIC_CONTENT_TYPES = {
    NewsContentType.STATIC_PAGE,
    NewsContentType.LIST_PAGE,
    NewsContentType.HUB_PAGE,
}
LOW_RELEVANCE_PENALTY = 50
_UNSET = object()


def calculate_importance(
    *,
    source: Source,
    title: str,
    raw_text: str = "",
    tags: Iterable[str] | None = None,
    franchises: Iterable[Franchise] | None = None,
    published_at=_UNSET,
    first_seen_at=None,
    content_type: str = NewsContentType.NEWS_ARTICLE,
    title_suspect: bool = False,
    date_confidence: str = "",
    nintendo_relevance_score: int | None = None,
) -> int:
    score, _reasons = calculate_importance_with_reasons(
        source=source,
        title=title,
        raw_text=raw_text,
        tags=tags,
        franchises=franchises,
        published_at=published_at,
        first_seen_at=first_seen_at,
        content_type=content_type,
        title_suspect=title_suspect,
        date_confidence=date_confidence,
        nintendo_relevance_score=nintendo_relevance_score,
    )
    return score


def calculate_importance_with_reasons(
    *,
    source: Source,
    title: str,
    raw_text: str = "",
    tags: Iterable[str] | None = None,
    franchises: Iterable[Franchise] | None = None,
    published_at=_UNSET,
    first_seen_at=None,
    content_type: str = NewsContentType.NEWS_ARTICLE,
    title_suspect: bool = False,
    date_confidence: str = "",
    nintendo_relevance_score: int | None = None,
) -> tuple[int, list[str]]:
    if is_boilerplate_title(title, source):
        return 0, ["보일러플레이트 의심: 공개 중요도 0"]

    score = source.base_score or TRUST_POINTS.get(source.trust_type, 5)
    reasons = [f"{source.trust_type_ko} 출처 +{score}"]
    normalized = normalize_title(f"{title} {raw_text}")
    raw_lower = f"{title} {raw_text}".lower()
    relevance = (
        nintendo_relevance_score
        if nintendo_relevance_score is not None
        else calculate_nintendo_relevance(source=source, title=title, raw_text=raw_text, tags=tags, franchises=franchises)
    )

    for keywords, points in KEYWORD_POINTS:
        if any(keyword.lower() in raw_lower or normalize_title(keyword) in normalized for keyword in keywords):
            score += points
            reasons.append(f"{keywords[0]} 포함 +{points}")

    tag_set = set(tags or [])
    if "switch2" in tag_set:
        score += 30
        reasons.append("Switch 2 태그 +30")
    if "direct" in tag_set:
        score += 20
        reasons.append("Direct 태그 +20")

    franchise_list = list(franchises or [])
    if any(franchise.priority >= 80 for franchise in franchise_list):
        score += 20
        reasons.append("우선순위 높은 게임종류 +20")
    elif franchise_list:
        score += 10
        reasons.append("게임종류 매칭 +10")

    if published_at is not _UNSET and published_at is None:
        score -= 20
        reasons.append("게시일 미상 -20")
    if first_seen_at is not None and published_at is not _UNSET and published_at and first_seen_at:
        try:
            if first_seen_at - published_at >= timedelta(days=14):
                score -= 20
                reasons.append("과거 기사 수집 -20")
        except TypeError:
            pass
    if content_type in STATIC_CONTENT_TYPES:
        score -= 40
        reasons.append("정적/목록/허브 페이지 -40")
    elif content_type == NewsContentType.ROUNDUP:
        score -= 30
        reasons.append("종합 정리글 -30")
    if title_suspect:
        score -= 20
        reasons.append("제목 추출 확인 필요 -20")
    if date_confidence == DateConfidence.LOW:
        score -= 20
        reasons.append("날짜 신뢰도 낮음 -20")
    if relevance < 3:
        penalty = LOW_RELEVANCE_PENALTY if relevance <= 1 else 25
        score -= penalty
        reasons.append(f"닌텐도 직접 관련성 낮음 -{penalty}")
    elif relevance >= 4 and source.trust_type == TrustType.OFFICIAL:
        reasons.append("닌텐도 공식/직접 관련성 높음")

    capped = max(0, min(100, score))
    if capped != score:
        reasons.append(f"상한 적용 {score} -> {capped}")
    return capped, reasons


def calculate_nintendo_relevance(
    *,
    source: Source,
    title: str,
    raw_text: str = "",
    tags: Iterable[str] | None = None,
    franchises: Iterable[Franchise] | None = None,
) -> int:
    normalized = normalize_title(f"{title} {raw_text[:500]}")
    tag_set = set(tags or [])
    franchise_list = list(franchises or [])

    if source.trust_type == TrustType.OFFICIAL:
        return 4
    if "direct" in tag_set or "switch2" in tag_set:
        return 3
    if franchise_list:
        return 3
    if any(marker in normalized for marker in ["nintendo switch", "switch2", "eshop", "nintendo direct", "mario", "zelda", "pokemon", "pokémon"]):
        return 3
    if "nintendo" in normalized:
        return 2
    if any(marker in normalized for marker in ["switch port", "switch version", "coming to switch", "switch release"]):
        return 2
    if any(marker in normalized for marker in ["playstation", "xbox", "capcom", "pc gaming", "steam"]):
        return 1
    return 0
