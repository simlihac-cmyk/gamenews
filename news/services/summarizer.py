from __future__ import annotations

from news.models import Source, TrustLabel

from .classifier import ClassificationResult
from .text import contains_hangul, extract_sentences, truncate


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
        if source.language == "ko" or contains_hangul(f"{title} {raw_text}"):
            sentences = extract_sentences(raw_text, limit=3)
            body = " ".join(sentences) if sentences else title
            return truncate(f"{context} {body}", 700)

        tag_labels = [TAG_LABELS[tag] for tag in classification.tags if tag in TAG_LABELS]
        tag_text = ", ".join(tag_labels[:4]) if tag_labels else "일반 닌텐도"
        return truncate(
            f"{context} 해외 출처에서 감지된 닌텐도 관련 소식입니다. "
            f"제목 기준으로 {tag_text} 관련 내용이 포함되어 있을 수 있습니다. "
            f"원제: “{title}”",
            700,
        )


def summarize_item(
    *,
    source: Source,
    title: str,
    raw_text: str,
    classification: ClassificationResult,
) -> str:
    return RuleBasedSummarizer().summarize(
        source=source,
        title=title,
        raw_text=raw_text,
        classification=classification,
    )

