from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from django.utils import timezone

from news.models import DateConfidence, NewsItem, PublishedAtPrecision, Source, TrustLabel

from .source_adapters import adapter_config
from .text import normalize_title, normalize_whitespace


MIN_TITLE_LENGTH = 10
LOW_CONFIDENCE_THRESHOLD = 50

BOILERPLATE_TITLE_EXACT = {
    "skip to main content",
    "read more",
    "news rss",
    "features rss",
    "reviews rss",
    "rss feed",
    "shop all",
    "characters hub",
    "smart device games",
    "monthly highlights",
    "my nintendo store shop all",
    "nintendo switch - oled model",
    "nintendo switch – oled model",
    "which nintendo switch is right for you",
    "tips and tricks",
    "creator's voice",
    "nintendo direct",
    "all nintendo switch games",
    "all games",
}

BOILERPLATE_TITLE_CONTAINS = {
    "shop all",
    "characters hub",
    "smart device games",
    "skip to ",
    "news rss",
    "all nintendo switch games",
}

GENERIC_SUMMARY_MARKERS = {
    "해외 출처에서 감지된 닌텐도 관련 소식입니다",
    "제목 기준으로",
    "원제:",
}

ARTICLE_PATH_MARKERS = {
    "/news",
    "/article",
    "/articles",
    "/whatsnew",
    "/release",
    "/schedule",
    "/event",
    "/interview",
    "/features",
}

HUB_PATH_PREFIXES = {
    "category",
    "categories",
    "tag",
    "tags",
    "topic",
    "topics",
    "platform",
    "platforms",
    "search",
    "page",
    "pages",
    "games",
    "tips",
    "tips-and-tricks",
}

HUB_PATH_EXACT_SUFFIXES = {
    "news/rss",
    "features/rss",
    "reviews/rss",
    "rss",
    "feed",
    "all-games",
    "nintendo-direct",
    "creators-voice",
    "creator-s-voice",
}

PUBLIC_EXCERPT_CHAR_LIMIT = 500


@dataclass(frozen=True)
class FranchiseMatch:
    franchise: object
    matched_alias: str
    confidence_score: int
    is_primary: bool = True


@dataclass(frozen=True)
class DateQuality:
    confidence: str
    is_suspect: bool
    reason: str = ""


def clean_title(title: str, source_slug: str | None = None) -> str:
    value = normalize_whitespace(title)
    value = re.sub(r"\s*\bRead more\b.*$", "", value, flags=re.IGNORECASE)
    value = re.sub(
        r"^(?:\d{1,2}/\d{1,2}/\d{2,4}|\d{4}[.-]\d{1,2}[.-]\d{1,2})\s*[-–—:·|]?\s*",
        "",
        value,
    )
    value = re.sub(r"^뉴스\s+\d{4}[.-]\d{1,2}[.-]\d{1,2}\s*[-–—:·|]?\s*", "", value)
    value = re.sub(r"\s+뉴스\s+\d{4}[.-]\d{1,2}[.-]\d{1,2}\s*$", "", value)
    value = re.sub(
        r"^(?:News|Guide|Feature|Video|Gallery|Review|Random|Talking Point|Rumou?r):\s+",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\s*(?:[-–—|]\s*)?(?:Nintendo Life|Nintendo Everything|VGC|Video Games Chronicle|Gematsu)\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    if len(value) > 120:
        value = re.sub(
            r"\s+(?:This month|This week|Check out|Learn more|Find out|Plus,|Now available|Here's what|Here are)\b.*$",
            "",
            value,
            flags=re.IGNORECASE,
        )
    if len(value) > 140:
        sentence_match = re.match(r"^(.{45,140}?[.!?])\s+[A-Z0-9가-힣]", value)
        if sentence_match:
            value = sentence_match.group(1)
    value = normalize_whitespace(value)
    return value


def is_boilerplate_title(title: str, source: Source | None = None) -> bool:
    normalized = normalize_whitespace(title).casefold()
    if not normalized:
        return True
    if _title_is_allowed(normalized, source):
        return False
    ascii_dash = normalized.replace("–", "-").replace("—", "-")
    if normalized in BOILERPLATE_TITLE_EXACT or ascii_dash in BOILERPLATE_TITLE_EXACT:
        return True
    if normalized.startswith("skip to"):
        return True
    if len(normalized) < MIN_TITLE_LENGTH:
        return True
    return any(marker in normalized for marker in BOILERPLATE_TITLE_CONTAINS)


def article_rejection_reason(
    *,
    title: str,
    url: str,
    raw_text: str = "",
    published_at=None,
    source: Source | None = None,
) -> str:
    raw_title = normalize_whitespace(title)
    display_title = clean_title(raw_title, getattr(source, "slug", None))
    if not raw_title:
        return "empty_title"
    if is_hub_url(url, source):
        return "hub_url"
    if is_boilerplate_title(raw_title, source) or is_boilerplate_title(display_title, source):
        if not display_title or len(normalize_whitespace(display_title)) < MIN_TITLE_LENGTH:
            return "too_short_title"
        return "boilerplate_title"
    date_quality = assess_date_quality(published_at)
    if date_quality.is_suspect:
        return "date_suspect"
    if is_boilerplate_body(raw_text):
        return "boilerplate_body"
    if not _looks_like_article_url(url, source) and published_at is None:
        return "non_article_url_without_date"
    return ""


def should_publish_item(
    *,
    title: str,
    url: str,
    raw_text: str = "",
    published_at=None,
    source: Source | None = None,
    extraction_confidence: int | None = None,
) -> bool:
    if article_rejection_reason(title=title, url=url, raw_text=raw_text, published_at=published_at, source=source):
        return False
    if extraction_confidence is not None and extraction_confidence < LOW_CONFIDENCE_THRESHOLD:
        return False
    return True


def extraction_confidence_for(
    *,
    title: str,
    url: str,
    raw_text: str = "",
    published_at=None,
    source: Source | None = None,
    franchise_count: int = 0,
) -> int:
    if article_rejection_reason(title=title, url=url, raw_text=raw_text, published_at=published_at, source=source):
        return 0

    score = 100
    cleaned = clean_title(title, getattr(source, "slug", None))
    if len(normalize_whitespace(cleaned)) < 18:
        score -= 25
    if published_at is None:
        score -= 15
    if not _looks_like_article_url(url, source):
        score -= 20
    if is_boilerplate_body(raw_text):
        score -= 30
    date_quality = assess_date_quality(published_at)
    if date_quality.confidence == DateConfidence.MEDIUM:
        score -= 10
    elif date_quality.confidence == DateConfidence.LOW:
        score -= 40
    if franchise_count >= 5:
        score -= 25
    return max(0, min(100, score))


def assess_date_quality(published_at, *, now=None) -> DateQuality:
    if published_at is None:
        return DateQuality(confidence=DateConfidence.MEDIUM, is_suspect=False)
    now = now or timezone.now()
    value = published_at
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone=timezone.get_current_timezone())
    if value.year < 1996:
        return DateQuality(confidence=DateConfidence.LOW, is_suspect=True, reason="before_1996")
    if value.year > now.year + 1:
        return DateQuality(confidence=DateConfidence.LOW, is_suspect=True, reason="year_too_far_future")
    if value > now + timedelta(hours=24):
        return DateQuality(confidence=DateConfidence.LOW, is_suspect=True, reason="future_more_than_24h")
    return DateQuality(confidence=DateConfidence.HIGH, is_suspect=False)


def date_quality_update_fields(published_at, *, now=None) -> dict[str, object]:
    result = assess_date_quality(published_at, now=now)
    return {
        "date_confidence": result.confidence,
        "is_date_suspect": result.is_suspect,
        "date_suspect_reason": result.reason,
    }


def is_generic_summary(summary: str) -> bool:
    value = normalize_whitespace(summary)
    if not value:
        return True
    return sum(1 for marker in GENERIC_SUMMARY_MARKERS if marker in value) >= 2


def fallback_summary_for(item: NewsItem) -> str:
    source_kind = "공식 출처" if item.trust_label == TrustLabel.OFFICIAL else "루머/유출성 출처" if item.trust_label == TrustLabel.RUMOR else "보도 출처"
    confirmation = {
        TrustLabel.OFFICIAL: "공식 출처에서 확인된 항목입니다.",
        TrustLabel.REPORTED: "전문 매체 보도 기준으로 분류된 항목입니다.",
        TrustLabel.RUMOR: "공식 확인 전인 루머/유출성 정보입니다.",
        TrustLabel.UNKNOWN: "아직 공식 확인 상태가 명확하지 않은 항목입니다.",
    }.get(item.trust_label, "확인 상태를 추가 점검해야 하는 항목입니다.")
    return (
        f"아직 상세 요약은 없지만, 제목과 {source_kind} 정보를 기준으로 분류된 소식입니다. "
        f"{confirmation} 원문에서 확인 후 요약이 보강될 예정입니다."
    )


def public_excerpt(raw_text: str, *, char_limit: int = PUBLIC_EXCERPT_CHAR_LIMIT, sentence_limit: int = 2) -> str:
    clean = normalize_whitespace(raw_text)
    if not clean:
        return ""
    pieces = re.split(r"(?<=[.!?。！？])\s+", clean)
    sentences = [piece.strip() for piece in pieces if piece.strip()]
    if sentences:
        clean = normalize_whitespace(" ".join(sentences[:sentence_limit]))
    if len(clean) <= char_limit:
        return clean
    return clean[: char_limit - 1].rstrip() + "…"


def is_backfill_item(published_at, first_seen_at, *, days: int = 14) -> bool:
    if not published_at or not first_seen_at:
        return False
    return first_seen_at - published_at >= timedelta(days=days)


def precision_for_datetime(value, raw_text: str = "") -> str:
    if value is None:
        return PublishedAtPrecision.UNKNOWN
    raw = normalize_whitespace(raw_text)
    if raw and re.fullmatch(r"(?:\d{4}[.-]\d{1,2}[.-]\d{1,2}|\d{1,2}/\d{1,2}/\d{2,4}|[A-Za-z]+ \d{1,2},? \d{4})", raw):
        return PublishedAtPrecision.DATE_ONLY
    return PublishedAtPrecision.EXACT


def is_hub_url(url: str, source: Source | None = None) -> bool:
    parsed = urlparse(url)
    path = (parsed.path or "").strip()
    if not path:
        return True
    path_lower = path.casefold()
    segments = [segment for segment in path_lower.strip("/").split("/") if segment]
    if not segments:
        return True
    if any(segment in HUB_PATH_PREFIXES for segment in segments[:2]):
        return True
    if any(path_lower.strip("/").endswith(suffix) for suffix in HUB_PATH_EXACT_SUFFIXES):
        return True
    query = parse_qs(parsed.query)
    if any(key.casefold() in {"s", "q", "search", "query", "paged", "page"} for key in query):
        return True

    config = adapter_config(source)
    lowered_url = url.casefold()
    return any(str(pattern).casefold() in lowered_url for pattern in config.get("reject_url_patterns", []))


def _title_is_allowed(normalized_title: str, source: Source | None) -> bool:
    if source is None:
        return False
    config = source.config or {}
    allow_exact = {normalize_whitespace(str(value)).casefold() for value in config.get("quality_allow_titles", [])}
    if normalized_title in allow_exact:
        return True
    return any(re.search(str(pattern), normalized_title, flags=re.IGNORECASE) for pattern in config.get("quality_allow_title_patterns", []))


def is_boilerplate_body(raw_text: str) -> bool:
    value = normalize_whitespace(raw_text).casefold()
    if not value:
        return False
    if value in BOILERPLATE_TITLE_EXACT:
        return True
    if len(value) <= 120 and any(marker in value for marker in BOILERPLATE_TITLE_CONTAINS):
        return True
    nav_hits = sum(1 for marker in ("shop all", "characters hub", "smart device games", "my nintendo store") if marker in value)
    return nav_hits >= 2


def _looks_like_article_url(url: str, source: Source | None = None) -> bool:
    parsed = urlparse(url)
    path = (parsed.path or "").lower()
    if not path:
        return False
    if is_hub_url(url, source):
        return False
    config = adapter_config(source)
    if source is not None and source.source_type in {"rss", "youtube_rss", "reddit_rss", "google_alert_rss"}:
        return path not in {"", "/"}
    include_patterns = [str(value).lower() for value in config.get("url_include_patterns", [])]
    if include_patterns:
        return any(pattern in path for pattern in include_patterns)
    if any(marker in path for marker in ARTICLE_PATH_MARKERS):
        return True
    title_path = normalize_title(path.replace("/", " "))
    return len(title_path.split()) >= 3
