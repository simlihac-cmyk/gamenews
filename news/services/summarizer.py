from __future__ import annotations

import logging
from typing import Any

import httpx
from django.conf import settings

from news.models import Source, TrustLabel

from .classifier import ClassificationResult
from .text import contains_hangul, extract_sentences, normalize_whitespace, truncate


logger = logging.getLogger(__name__)


TRUST_CONTEXT = {
    TrustLabel.OFFICIAL: "공식 출처에서 확인된 소식입니다.",
    TrustLabel.REPORTED: "해외 매체 보도입니다.",
    TrustLabel.RUMOR: "공식 확인 전인 루머/유출성 정보입니다.",
    TrustLabel.UNKNOWN: "출처 신뢰도를 추가 확인해야 하는 소식입니다.",
}

TAG_LABELS = {
    "direct": "닌텐도 다이렉트",
    "release_date": "발매일/출시일",
    "trailer": "트레일러",
    "new_game": "신작 발표",
    "update": "업데이트",
    "sale": "세일",
    "rumor": "루머",
    "leak": "유출",
    "switch": "Nintendo Switch",
    "switch2": "Nintendo Switch 2",
}

SUMMARY_INSTRUCTIONS = (
    "너는 Nintendo Watch의 한국어 뉴스 요약자다. "
    "원문을 길게 번역하거나 복사하지 말고, 제공된 제목/본문/출처만 근거로 2~4문장의 짧은 한국어 자체 요약을 작성한다. "
    "과장하지 말고 루머/유출은 공식 확인 전임을 분명히 밝힌다. "
    "출력은 반드시 다음 네 줄 형식으로 작성한다: "
    "무슨 일?: ...\n왜 중요?: ...\n확인 상태: ...\n주의: ..."
)


class RuleBasedSummarizer:
    def summarize(
        self,
        *,
        source: Source,
        title: str,
        raw_text: str,
        classification: ClassificationResult,
    ) -> str:
        context = TRUST_CONTEXT.get(classification.trust_label, TRUST_CONTEXT[TrustLabel.UNKNOWN])
        status = _status_sentence(classification.trust_label)
        why = _why_it_matters(classification)
        if source.language == "ko" or contains_hangul(f"{title} {raw_text}"):
            sentences = extract_sentences(raw_text, limit=3)
            body = " ".join(sentences) if sentences else title
            what = body if body != title else f"{title} 관련 소식입니다."
            return _format_summary(what=what, why=why, status=status, caution=_caution_sentence(classification.trust_label))

        tag_labels = [TAG_LABELS[tag] for tag in classification.tags if tag in TAG_LABELS]
        tag_text = ", ".join(tag_labels[:4]) if tag_labels else "일반 닌텐도"
        source_excerpt = _english_excerpt(raw_text)
        what = (
            f"해외 출처에서 '{title}' 항목이 수집됐습니다."
            if not source_excerpt
            else f"해외 출처의 '{title}' 항목으로, 원문 발췌 기준 '{source_excerpt}' 내용이 확인됩니다."
        )
        why = f"{tag_text} 관련 흐름으로 분류되어 추적 가치가 있습니다."
        return _format_summary(what=what, why=why, status=f"{context} {status}", caution=_caution_sentence(classification.trust_label))


class OpenAIKoreanSummarizer:
    def summarize(
        self,
        *,
        source: Source,
        title: str,
        raw_text: str,
        classification: ClassificationResult,
    ) -> str:
        api_key = settings.SUMMARY_OPENAI_API_KEY
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        source_text = truncate(raw_text, settings.SUMMARY_MAX_SOURCE_CHARS)
        prompt = "\n".join(
            [
                f"제목: {title}",
                f"출처: {source.name} ({source.trust_type_ko})",
                f"신뢰 라벨: {classification.trust_label}",
                f"카테고리/태그: {classification.category}, {', '.join(classification.tags)}",
                "원문 발췌:",
                source_text or "(본문 없음)",
            ]
        )
        response = httpx.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.SUMMARY_OPENAI_MODEL,
                "instructions": SUMMARY_INSTRUCTIONS,
                "input": prompt,
                "max_output_tokens": 450,
            },
            timeout=settings.SUMMARY_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        summary = _extract_openai_text(response.json())
        if not summary:
            raise RuntimeError("OpenAI response did not contain output text")
        return truncate(_normalize_generated_summary(summary), 900)


def summarize_item(
    *,
    source: Source,
    title: str,
    raw_text: str,
    classification: ClassificationResult,
    provider: str | None = None,
) -> str:
    provider = (provider or settings.SUMMARY_PROVIDER or "rules").lower()
    if provider in {"openai", "auto"}:
        try:
            return OpenAIKoreanSummarizer().summarize(
                source=source,
                title=title,
                raw_text=raw_text,
                classification=classification,
            )
        except Exception as exc:  # noqa: BLE001 - summary failure should not block collection.
            if provider == "openai":
                logger.warning("OpenAI summarization failed; falling back to rules source=%s title=%s error=%s", source.slug, title, exc)
            else:
                logger.debug("OpenAI summarization unavailable; using rules source=%s title=%s error=%s", source.slug, title, exc)
    return RuleBasedSummarizer().summarize(
        source=source,
        title=title,
        raw_text=raw_text,
        classification=classification,
    )


def _format_summary(*, what: str, why: str, status: str, caution: str) -> str:
    return truncate(
        "\n".join(
            [
                f"무슨 일?: {normalize_whitespace(what)}",
                f"왜 중요?: {normalize_whitespace(why)}",
                f"확인 상태: {normalize_whitespace(status)}",
                f"주의: {normalize_whitespace(caution)}",
            ]
        ),
        900,
    )


def _status_sentence(trust_label: str) -> str:
    return {
        TrustLabel.OFFICIAL: "공식 출처에서 확인된 내용입니다.",
        TrustLabel.REPORTED: "전문 매체 보도 기준으로 확인된 내용입니다.",
        TrustLabel.RUMOR: "공식 확인 전인 루머/유출성 정보입니다.",
        TrustLabel.UNKNOWN: "출처와 사실관계를 추가 확인해야 합니다.",
    }.get(trust_label, "출처와 사실관계를 추가 확인해야 합니다.")


def _caution_sentence(trust_label: str) -> str:
    if trust_label == TrustLabel.RUMOR:
        return "루머성 정보이므로 공식 발표 전까지 사실로 단정하지 마세요."
    if trust_label == TrustLabel.OFFICIAL:
        return "원문 공지의 세부 조건과 지역 차이는 원문 링크에서 확인하세요."
    return "자동 요약이므로 세부 표현과 맥락은 원문 링크에서 확인하세요."


def _why_it_matters(classification: ClassificationResult) -> str:
    labels = [TAG_LABELS[tag] for tag in classification.tags if tag in TAG_LABELS]
    if labels:
        return f"{', '.join(labels[:4])} 관련 소식이라 닌텐도 일정과 관심작 흐름을 파악하는 데 도움이 됩니다."
    return "닌텐도 관련 새 소식으로 분류되어 추적할 가치가 있습니다."


def _english_excerpt(raw_text: str) -> str:
    sentences = extract_sentences(raw_text, limit=1)
    if not sentences:
        return ""
    return truncate(sentences[0], 180)


def _extract_openai_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    pieces: list[str] = []
    for item in payload.get("output", []) or []:
        for content in item.get("content", []) or []:
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                pieces.append(normalize_whitespace(str(content["text"])))
    return "\n".join(piece for piece in pieces if piece)


def _normalize_generated_summary(value: str) -> str:
    lines = [normalize_whitespace(line) for line in value.splitlines() if normalize_whitespace(line)]
    if not lines:
        return ""
    required = ["무슨 일?:", "왜 중요?:", "확인 상태:", "주의:"]
    if all(any(line.startswith(prefix) for line in lines) for prefix in required):
        return "\n".join(lines[:4])
    return _format_summary(
        what=lines[0],
        why=lines[1] if len(lines) > 1 else "닌텐도 관련 흐름을 파악하는 데 도움이 됩니다.",
        status=lines[2] if len(lines) > 2 else "출처 기준으로 자동 요약된 내용입니다.",
        caution=lines[3] if len(lines) > 3 else "자동 요약이므로 세부 내용은 원문 링크에서 확인하세요.",
    )
