from __future__ import annotations

import re
from dataclasses import dataclass

from news.models import Franchise, NewsCategory, Source, TrustLabel, TrustType

from .quality import FranchiseMatch
from .text import normalize_title


@dataclass(frozen=True)
class ClassificationResult:
    trust_label: str
    category: str
    tags: list[str]
    confidence_score: int
    trust_reasons: list[str]


KEYWORDS = {
    "direct": ["nintendo direct", "direct", "닌텐도 다이렉트", "ニンテンドーダイレクト"],
    "release_date": ["release date", "launch date", "발매일", "출시일", "発売日"],
    "trailer": ["trailer", "트레일러", "pv", "영상 공개"],
    "new_game": ["announced", "reveal", "revealed", "unveiled", "신작", "발표", "공개"],
    "rumor": ["rumor", "rumour", "insider", "reportedly", "루머", "미확인"],
    "leak": ["leak", "leaked", "유출"],
    "sale": ["sale", "discount", "eshop sale", "세일", "할인"],
    "update": ["update", "patch", "dlc", "업데이트", "패치"],
    "switch2": ["switch2", "switch 2", "nintendo switch 2", "차세대기"],
    "switch": ["switch", "nintendo switch", "닌텐도 스위치"],
}

CATEGORY_PRIORITY = [
    "direct",
    "release_date",
    "trailer",
    "new_game",
    "leak",
    "rumor",
    "sale",
    "update",
]


def classify_item(source: Source, title: str, raw_text: str = "") -> ClassificationResult:
    trust_label = classify_trust_label(source)
    combined = f"{title} {raw_text}".lower()
    normalized = normalize_title(combined)
    tags = detect_tags(combined, normalized)

    category = NewsCategory.GENERAL
    for candidate in CATEGORY_PRIORITY:
        if candidate in tags:
            category = candidate
            break
    if category == NewsCategory.GENERAL:
        if trust_label == TrustLabel.OFFICIAL:
            category = NewsCategory.OFFICIAL
        elif trust_label == TrustLabel.RUMOR:
            category = NewsCategory.RUMOR

    confidence = {
        TrustLabel.OFFICIAL: 90,
        TrustLabel.REPORTED: 70,
        TrustLabel.RUMOR: 45,
        TrustLabel.UNKNOWN: 35,
    }.get(trust_label, 35)
    trust_reasons = trust_reasons_for(source, trust_label)
    if tags:
        confidence = min(100, confidence + min(len(tags) * 3, 10))

    return ClassificationResult(
        trust_label=trust_label,
        category=category,
        tags=tags,
        confidence_score=confidence,
        trust_reasons=trust_reasons,
    )


def classify_trust_label(source: Source) -> str:
    if source.trust_type == TrustType.OFFICIAL:
        return TrustLabel.OFFICIAL
    if source.trust_type == TrustType.PRESS:
        return TrustLabel.REPORTED
    if source.trust_type == TrustType.RUMOR:
        return TrustLabel.RUMOR
    return TrustLabel.UNKNOWN


def trust_reasons_for(source: Source, trust_label: str) -> list[str]:
    if trust_label == TrustLabel.OFFICIAL:
        return ["공식 출처에서 수집됨"]
    if trust_label == TrustLabel.REPORTED:
        return ["전문 매체 보도 출처"]
    if trust_label == TrustLabel.RUMOR:
        return ["루머/유출 커뮤니티 또는 RSS 경유", "공식 확인 전"]
    return [f"{source.name} 출처 기준 확인 상태 미정"]


def detect_tags(lower_text: str, normalized_text: str | None = None) -> list[str]:
    normalized_text = normalized_text or normalize_title(lower_text)
    tags: list[str] = []
    for tag, keywords in KEYWORDS.items():
        for keyword in keywords:
            if _keyword_matches(keyword, lower_text, normalized_text):
                tags.append(tag)
                break
    return tags


def _keyword_matches(keyword: str, lower_text: str, normalized_text: str) -> bool:
    keyword_norm = normalize_title(keyword)
    if not keyword_norm:
        return False
    if re.fullmatch(r"[a-z0-9 ]+", keyword_norm):
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(keyword_norm)}(?![a-z0-9])", normalized_text))
    return keyword.lower() in lower_text or keyword_norm in normalized_text


def detect_franchises(title: str, raw_text: str = "") -> list[Franchise]:
    return [match.franchise for match in detect_franchise_matches(title, raw_text)]


def detect_franchise_matches(title: str, raw_text: str = "") -> list[FranchiseMatch]:
    raw_haystack = f"{title} {raw_text}"
    title_haystack = normalize_title(title)
    first_sentence = _first_sentence(raw_text)
    first_sentence_haystack = normalize_title(first_sentence)
    haystack = normalize_title(raw_haystack)
    matches: list[FranchiseMatch] = []
    for franchise in Franchise.objects.order_by("-priority", "name"):
        aliases = sorted([franchise.name, *(franchise.aliases or [])], key=lambda value: len(normalize_title(value)), reverse=True)
        for alias in aliases:
            title_confidence = _alias_match_confidence(title_haystack, title, alias)
            first_sentence_confidence = _alias_match_confidence(first_sentence_haystack, first_sentence, alias)
            body_confidence = _alias_match_confidence(haystack, raw_haystack, alias)
            confidence = max(title_confidence, first_sentence_confidence, body_confidence)
            if confidence:
                occurrence_count = _alias_occurrence_count(haystack, alias)
                has_event_context = _has_primary_context(f"{title} {first_sentence}")
                is_primary = bool(
                    title_confidence
                    or (first_sentence_confidence and has_event_context)
                    or (occurrence_count >= 2 and _has_primary_context(raw_haystack))
                )
                adjusted_confidence = confidence if is_primary else min(confidence, 45)
                matches.append(
                    FranchiseMatch(
                        franchise=franchise,
                        matched_alias=alias,
                        confidence_score=adjusted_confidence,
                        is_primary=is_primary,
                    )
                )
                break
    return matches


def _alias_matches(haystack: str, alias: str) -> bool:
    return bool(_alias_match_confidence(haystack, haystack, alias))


def _alias_match_confidence(haystack: str, raw_haystack: str, alias: str) -> int:
    needle = normalize_title(alias)
    if not needle:
        return 0
    if re.fullmatch(r"[a-z0-9]{1,2}", needle):
        if not re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", raw_haystack):
            return 0
        if not any(context in haystack for context in ["nintendo", "switch", "donkey", "kong"]):
            return 0
        return 55
    if re.fullmatch(r"[a-z0-9 ]+", needle):
        return 100 if re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) else 0
    return 100 if needle in haystack else 0


def _alias_occurrence_count(haystack: str, alias: str) -> int:
    needle = normalize_title(alias)
    if not needle:
        return 0
    if re.fullmatch(r"[a-z0-9 ]+", needle):
        return len(re.findall(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack))
    return haystack.count(needle)


def _first_sentence(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?。！？])\s+", text)
    return parts[0][:260]


def _has_primary_context(value: str) -> bool:
    normalized = normalize_title(value)
    return any(
        marker in normalized
        for marker in [
            "announce",
            "announced",
            "reveal",
            "revealed",
            "release",
            "launch",
            "trailer",
            "update",
            "dlc",
            "bundle",
            "direct",
            "movie",
            "game",
            "발표",
            "공개",
            "발매",
            "출시",
            "업데이트",
        ]
    )
