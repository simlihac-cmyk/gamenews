from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from news.models import NewsItem
from news.services.quality import LOW_CONFIDENCE_THRESHOLD, article_rejection_reason, is_generic_summary
from news.services.text import normalize_whitespace, truncate


BATCH_SCHEMA = "nintendowatch_summary_batch_v3"
SUMMARY_PREFIXES = ("무슨 일?:", "왜 중요?:", "확인 상태:", "주의:")
DEFAULT_MIN_RAW_CHARS = 160
DETAILED_IMPORTANCE_THRESHOLD = 75
MAX_IMPORTED_SUMMARY_CHARS = 1800


@dataclass(frozen=True)
class ImportedSummary:
    item_id: int
    token: str
    summary_ko: str


def summary_token_for(item: NewsItem) -> str:
    payload = f"{item.pk}|{item.title}|{item.canonical_url or item.url}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def build_summary_batch_prompt(
    items: Iterable[NewsItem],
    *,
    target: str = "generic",
    max_source_chars: int = 1800,
    min_raw_chars: int = DEFAULT_MIN_RAW_CHARS,
    detailed_threshold: int = DETAILED_IMPORTANCE_THRESHOLD,
) -> str:
    payload = {
        "schema": BATCH_SCHEMA,
        "target": target,
        "detailed_threshold": detailed_threshold,
        "items": [
            summary_export_payload(
                item,
                max_source_chars=max_source_chars,
                min_raw_chars=min_raw_chars,
                detailed_threshold=detailed_threshold,
            )
            for item in items
        ],
    }
    target_name = {"chatgpt": "ChatGPT", "gemini": "Gemini"}.get(target, "LLM")
    return "\n".join(
        [
            "# Nintendo Watch 한국어 요약 배치",
            "",
            f"아래 JSON의 `items`를 {target_name}에서 한국어로 요약해 주세요.",
            "",
            "규칙:",
            "- 원문을 길게 번역하거나 복사하지 말고, 제공된 제목/출처/발췌만 근거로 자체 요약을 작성하세요.",
            "- `raw_excerpt`에 없는 가격, 날짜, 지역, 수량, 공식 확인 여부를 추정하지 마세요.",
            "- `quality_notes`가 비어 있지 않으면 해당 항목은 원문 품질 확인이 필요한 항목이므로 단정적인 표현을 피하세요.",
            "- `summary_mode`가 `brief`이면 `summary_ko`는 반드시 네 줄 형식으로 작성하세요: `무슨 일?:`, `왜 중요?:`, `확인 상태:`, `주의:`",
            "- `summary_mode`가 `detailed`이면 원문 URL을 열 수 있는 경우 `source_url`을 직접 확인해 더 구체적으로 쓰세요.",
            "- `summary_mode`가 `detailed`인 항목은 반드시 다음 형식을 사용하세요: `무슨 일?:`, `핵심 내용:`, `- ...`, `왜 중요?:`, `확인 상태:`, `주의:`",
            "- `source_url`을 열 수 없으면 제공된 `raw_excerpt`만 사용하고, `주의:`에 원문 직접 확인이 필요하다고 쓰세요.",
            "- 루머/유출/미확인 항목은 공식 확인 전이라는 점을 분명히 쓰세요.",
            "- 과장하거나 입력에 없는 사실을 만들지 마세요.",
            "- `id`와 `token`은 절대 바꾸지 마세요.",
            "",
            "응답은 설명 없이 아래 형태의 유효한 JSON만 반환하세요.",
            "",
            "```json",
            (
                '{"summaries":['
                '{"id":123,"token":"brief-example","summary_ko":"무슨 일?: ...\\n왜 중요?: ...\\n확인 상태: ...\\n주의: ..."},'
                '{"id":124,"token":"detailed-example","summary_ko":"무슨 일?: ...\\n핵심 내용:\\n- ...\\n- ...\\n- ...\\n왜 중요?: ...\\n확인 상태: ...\\n주의: ..."}'
                "]}"
            ),
            "```",
            "",
            "입력 JSON:",
            "",
            "```json",
            json.dumps(payload, ensure_ascii=False, indent=2),
            "```",
        ]
    )


def summary_export_payload(
    item: NewsItem,
    *,
    max_source_chars: int = 1800,
    min_raw_chars: int = DEFAULT_MIN_RAW_CHARS,
    detailed_threshold: int = DETAILED_IMPORTANCE_THRESHOLD,
) -> dict[str, Any]:
    raw_text = item.raw_item.raw_text or item.summary_original
    raw_excerpt = truncate(raw_text, max_source_chars)
    metadata = item.raw_item.metadata or {}
    source_url = item.canonical_url or item.url
    summary_mode = "detailed" if item.importance_score >= detailed_threshold else "brief"
    return {
        "id": item.pk,
        "token": summary_token_for(item),
        "summary_mode": summary_mode,
        "summary_instruction": _summary_instruction(summary_mode),
        "title": item.title,
        "raw_title": item.raw_item.title,
        "source": item.source.name,
        "source_group": item.source.source_group_ko,
        "trust_label": item.trust_label,
        "trust_label_ko": item.trust_label_ko,
        "category": item.category,
        "category_ko": item.category_ko,
        "tags": item.detected_tags or [],
        "importance_score": item.importance_score,
        "trust_score": item.confidence_score,
        "extraction_confidence": item.extraction_confidence,
        "published_at": item.published_at.isoformat() if item.published_at else "",
        "raw_published_at_text": item.raw_item.raw_published_at_text,
        "url": item.url,
        "canonical_url": item.canonical_url or "",
        "source_url": source_url,
        "web_research_recommended": summary_mode == "detailed",
        "original_source": metadata.get("original_source", ""),
        "display_source": metadata.get("display_source", item.source.name),
        "raw_excerpt_chars": len(normalize_whitespace(raw_text)),
        "quality_notes": summary_export_rejection_reasons(item, min_raw_chars=min_raw_chars),
        "raw_excerpt": raw_excerpt,
    }


def parse_summary_batch_response(value: str) -> list[ImportedSummary]:
    data = _extract_json(value)
    if isinstance(data, dict):
        entries = data.get("summaries")
    else:
        entries = data
    if not isinstance(entries, list):
        raise ValueError("summary response must contain a summaries list")

    summaries: list[ImportedSummary] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each summary entry must be an object")
        item_id = entry.get("id") or entry.get("item_id")
        token = normalize_whitespace(str(entry.get("token") or ""))
        summary = entry.get("summary_ko") or entry.get("summary") or ""
        if not item_id or not token or not summary:
            raise ValueError("summary entry requires id, token, and summary_ko")
        summaries.append(
            ImportedSummary(
                item_id=int(item_id),
                token=token,
                summary_ko=normalize_imported_summary(str(summary)),
            )
        )
    return summaries


def normalize_imported_summary(value: str) -> str:
    lines = [normalize_whitespace(line) for line in value.splitlines() if normalize_whitespace(line)]
    if not all(any(line.startswith(prefix) for line in lines) for prefix in SUMMARY_PREFIXES):
        raise ValueError("summary_ko must include the four required Korean summary lines")
    if any(line.startswith("핵심 내용:") for line in lines):
        return _truncate_preserving_lines("\n".join(lines), MAX_IMPORTED_SUMMARY_CHARS)
    ordered: list[str] = []
    for prefix in SUMMARY_PREFIXES:
        match = next(line for line in lines if line.startswith(prefix))
        ordered.append(match)
    return "\n".join(ordered)


def should_export_for_summary(
    item: NewsItem,
    *,
    force: bool = False,
    include_low_quality: bool = False,
    min_raw_chars: int = DEFAULT_MIN_RAW_CHARS,
) -> bool:
    has_useful_summary = item.summary_ko and not is_generic_summary(item.summary_ko)
    if has_useful_summary and not force:
        return False
    if include_low_quality:
        return True
    return not summary_export_rejection_reasons(item, min_raw_chars=min_raw_chars)


def summary_export_rejection_reasons(item: NewsItem, *, min_raw_chars: int = DEFAULT_MIN_RAW_CHARS) -> list[str]:
    raw_text = item.raw_item.raw_text or item.summary_original
    reasons: list[str] = []
    if item.is_archived:
        reasons.append("archived")
    if item.is_date_suspect:
        reasons.append("date_suspect")
    if item.raw_item.rejection_reason:
        reasons.append(f"raw_rejected:{item.raw_item.rejection_reason}")
    if item.extraction_confidence < LOW_CONFIDENCE_THRESHOLD:
        reasons.append("low_extraction_confidence")
    article_reason = article_rejection_reason(
        title=item.raw_item.title or item.title,
        url=item.canonical_url or item.url,
        raw_text=raw_text,
        published_at=item.published_at,
        source=item.source,
    )
    if article_reason:
        reasons.append(article_reason)
    clean_raw = normalize_whitespace(raw_text)
    if len(clean_raw) < min_raw_chars:
        reasons.append("raw_text_too_short")
    if clean_raw and normalize_whitespace(item.raw_item.title).casefold() == clean_raw.casefold():
        reasons.append("raw_text_same_as_title")
    return _dedupe(reasons)


def token_matches(item: NewsItem, token: str) -> bool:
    return summary_token_for(item) == token


def _summary_instruction(summary_mode: str) -> str:
    if summary_mode == "detailed":
        return (
            "중요도 75점 이상 항목입니다. 가능하면 source_url 원문을 직접 열어 확인한 뒤, "
            "무슨 일/핵심 내용 2~4개/왜 중요/확인 상태/주의를 구체적으로 작성하세요."
        )
    return "낮거나 중간 중요도 항목입니다. 현재처럼 간결한 4줄 요약으로 충분합니다."


def _truncate_preserving_lines(value: str, length: int) -> str:
    if len(value) <= length:
        return value
    return value[: length - 1].rstrip() + "…"


def _extract_json(value: str) -> Any:
    text = value.strip()
    for candidate in _json_candidates(text):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ValueError("could not find valid JSON in summary response")


def _json_candidates(text: str) -> list[str]:
    candidates = [text]
    candidates.extend(match.group(1).strip() for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE))
    object_start = text.find("{")
    object_end = text.rfind("}")
    if object_start >= 0 and object_end > object_start:
        candidates.append(text[object_start : object_end + 1])
    array_start = text.find("[")
    array_end = text.rfind("]")
    if array_start >= 0 and array_end > array_start:
        candidates.append(text[array_start : array_end + 1])
    return candidates


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
