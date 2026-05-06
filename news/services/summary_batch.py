from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from news.models import NewsItem
from news.services.quality import is_generic_summary
from news.services.text import normalize_whitespace, truncate


BATCH_SCHEMA = "nintendowatch_summary_batch_v1"
SUMMARY_PREFIXES = ("무슨 일?:", "왜 중요?:", "확인 상태:", "주의:")


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
) -> str:
    payload = {
        "schema": BATCH_SCHEMA,
        "target": target,
        "items": [summary_export_payload(item, max_source_chars=max_source_chars) for item in items],
    }
    target_name = {"chatgpt": "ChatGPT", "gemini": "Gemini"}.get(target, "LLM")
    return "\n".join(
        [
            "# Nintendo Watch 한국어 요약 배치",
            "",
            f"아래 JSON의 `items`를 {target_name}에서 한국어로 짧게 요약해 주세요.",
            "",
            "규칙:",
            "- 원문을 길게 번역하거나 복사하지 말고, 제공된 제목/출처/발췌만 근거로 자체 요약을 작성하세요.",
            "- 각 `summary_ko`는 반드시 네 줄 형식으로 작성하세요: `무슨 일?:`, `왜 중요?:`, `확인 상태:`, `주의:`",
            "- 루머/유출/미확인 항목은 공식 확인 전이라는 점을 분명히 쓰세요.",
            "- 과장하거나 입력에 없는 사실을 만들지 마세요.",
            "- `id`와 `token`은 절대 바꾸지 마세요.",
            "",
            "응답은 설명 없이 아래 형태의 유효한 JSON만 반환하세요.",
            "",
            "```json",
            '{"summaries":[{"id":123,"token":"example","summary_ko":"무슨 일?: ...\\n왜 중요?: ...\\n확인 상태: ...\\n주의: ..."}]}',
            "```",
            "",
            "입력 JSON:",
            "",
            "```json",
            json.dumps(payload, ensure_ascii=False, indent=2),
            "```",
        ]
    )


def summary_export_payload(item: NewsItem, *, max_source_chars: int = 1800) -> dict[str, Any]:
    raw_text = item.raw_item.raw_text or item.summary_original
    return {
        "id": item.pk,
        "token": summary_token_for(item),
        "title": item.title,
        "source": item.source.name,
        "trust_label": item.trust_label,
        "trust_label_ko": item.trust_label_ko,
        "category": item.category,
        "category_ko": item.category_ko,
        "tags": item.detected_tags or [],
        "importance_score": item.importance_score,
        "trust_score": item.confidence_score,
        "published_at": item.published_at.isoformat() if item.published_at else "",
        "url": item.url,
        "raw_excerpt": truncate(raw_text, max_source_chars),
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
    if all(any(line.startswith(prefix) for line in lines) for prefix in SUMMARY_PREFIXES):
        ordered: list[str] = []
        for prefix in SUMMARY_PREFIXES:
            match = next(line for line in lines if line.startswith(prefix))
            ordered.append(match)
        return "\n".join(ordered)
    raise ValueError("summary_ko must include the four required Korean summary lines")


def should_export_for_summary(item: NewsItem, *, force: bool = False) -> bool:
    if force:
        return True
    return not item.summary_ko or is_generic_summary(item.summary_ko)


def token_matches(item: NewsItem, token: str) -> bool:
    return summary_token_for(item) == token


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
