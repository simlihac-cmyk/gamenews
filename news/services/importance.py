from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable
from datetime import timedelta
from typing import Union

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

ReasonPayload = Union[str, dict[str, str]]


@dataclass(frozen=True)
class ScoreReason:
    code: str
    label: str

    def as_json(self) -> dict[str, str]:
        return {"code": self.code, "label": self.label}


@dataclass(frozen=True)
class ScoreResult:
    score: int
    reasons: list[dict[str, str]]


def score_reason(code: str, label: str) -> dict[str, str]:
    return ScoreReason(code=code, label=label).as_json()


def reason_labels(reasons: Iterable[ReasonPayload] | None) -> list[str]:
    labels: list[str] = []
    for reason in reasons or []:
        if isinstance(reason, dict):
            label = str(reason.get("label") or reason.get("code") or "").strip()
        else:
            label = str(reason).strip()
        if not label or label == "재계산 필요":
            continue
        labels.append(label)
    return labels


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
    result = calculate_importance_result(
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
    return result.score


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
) -> tuple[int, list[dict[str, str]]]:
    result = calculate_importance_result(
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
    return result.score, result.reasons


def calculate_importance_result(
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
) -> ScoreResult:
    if is_boilerplate_title(title, source):
        return ScoreResult(0, [score_reason("boilerplate_title", "보일러플레이트 의심: 공개 중요도 0")])

    score = source.base_score or TRUST_POINTS.get(source.trust_type, 5)
    reasons = [score_reason("source_base", f"{source.trust_type_ko} 출처 +{score}")]
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
            reasons.append(score_reason(f"keyword_{normalize_title(keywords[0]).replace(' ', '_')}", f"{keywords[0]} 포함 +{points}"))

    tag_set = set(tags or [])
    if "switch2" in tag_set:
        score += 30
        reasons.append(score_reason("switch2_tag", "Switch 2 태그 +30"))
    if "direct" in tag_set:
        score += 20
        reasons.append(score_reason("direct_tag", "Direct 태그 +20"))

    franchise_list = list(franchises or [])
    if any(franchise.priority >= 80 for franchise in franchise_list):
        score += 20
        reasons.append(score_reason("high_priority_game_type", "우선순위 높은 게임종류 +20"))
    elif franchise_list:
        score += 10
        reasons.append(score_reason("game_type_match", "게임종류 매칭 +10"))

    if published_at is not _UNSET and published_at is None:
        score -= 20
        reasons.append(score_reason("missing_published_at", "게시일 미상 -20"))
    if first_seen_at is not None and published_at is not _UNSET and published_at and first_seen_at:
        try:
            if first_seen_at - published_at >= timedelta(days=14):
                score -= 20
                reasons.append(score_reason("old_article_collected_late", "과거 기사 수집 -20"))
        except TypeError:
            pass
    if content_type in STATIC_CONTENT_TYPES:
        score -= 40
        reasons.append(score_reason("static_list_hub_penalty", "정적/목록/허브 페이지 -40"))
    elif content_type == NewsContentType.ROUNDUP:
        score -= 30
        reasons.append(score_reason("roundup_penalty", "종합 정리글 -30"))
    if title_suspect:
        score -= 20
        reasons.append(score_reason("title_suspect_penalty", "제목 추출 확인 필요 -20"))
    if date_confidence in {DateConfidence.LOW, DateConfidence.MEDIUM}:
        score -= 20
        reason_code = "date_confidence_low" if date_confidence == DateConfidence.LOW else "date_confidence_unknown"
        reason_label = "날짜 신뢰도 낮음 -20" if date_confidence == DateConfidence.LOW else "게시일 확인 불충분 -20"
        reasons.append(score_reason(reason_code, reason_label))
    if relevance < 3:
        penalty = LOW_RELEVANCE_PENALTY if relevance <= 1 else 25
        score -= penalty
        reasons.append(score_reason("low_nintendo_relevance", f"닌텐도 직접 관련성 낮음 -{penalty}"))
    elif relevance >= 4 and source.trust_type == TrustType.OFFICIAL:
        reasons.append(score_reason("official_high_nintendo_relevance", "닌텐도 공식/직접 관련성 높음"))

    capped = max(0, min(100, score))
    if capped != score:
        reasons.append(score_reason("score_capped", f"상한 적용 {score} -> {capped}"))
    return ScoreResult(capped, reasons)


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
    competitor_markers = ["playstation", "xbox", "capcom", "pc gaming", "steam"]
    nintendo_platform_markers = ["nintendo switch", "switch2", "switch 2", "eshop", "nintendo direct"]
    first_party_markers = ["mario", "zelda", "pokemon", "pokémon", "kirby", "metroid", "splatoon", "donkey kong", "fire emblem", "xenoblade"]

    if source.trust_type == TrustType.OFFICIAL:
        return 4
    if "direct" in tag_set or "switch2" in tag_set:
        if any(marker in normalized for marker in competitor_markers) and not any(marker in normalized for marker in ["coming to switch", "switch release", "nintendo switch", "switch2", "switch 2"]):
            return 1
        return 3
    if franchise_list:
        return 3
    if any(marker in normalized for marker in competitor_markers) and not any(marker in normalized for marker in nintendo_platform_markers):
        return 1
    if any(marker in normalized for marker in nintendo_platform_markers + first_party_markers):
        return 3
    if "nintendo" in normalized:
        return 2
    if any(marker in normalized for marker in ["switch port", "switch version", "coming to switch", "switch release"]):
        return 2
    if any(marker in normalized for marker in competitor_markers):
        return 1
    return 0
