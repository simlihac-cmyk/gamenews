from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from django.utils import timezone

from news.models import DateConfidence, NewsContentType, NewsItem, PublishedAtPrecision, Source, TrustLabel, TrustType

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
TITLE_SUSPECT_LENGTH = 120
STATIC_CONTENT_TYPES = {
    NewsContentType.STATIC_PAGE,
    NewsContentType.LIST_PAGE,
    NewsContentType.HUB_PAGE,
}

BODY_START_PATTERNS = [
    r"This month,",
    r"Whether you(?:'|’)re",
    r"Check out",
    r"The .{1,80}? game",
    r"Get ready",
    r"Available now",
    r"Starting today",
    r"In this article",
    r"Learn more",
    r"Read more",
]
BODY_START_RE = re.compile(r"\s+(?:" + "|".join(BODY_START_PATTERNS) + r").*$", flags=re.IGNORECASE)
BODY_START_ANYWHERE_RE = re.compile(r"(?:" + "|".join(BODY_START_PATTERNS) + r")", flags=re.IGNORECASE)


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


@dataclass(frozen=True)
class TitleQuality:
    cleaned: str
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
    value = _strip_body_start_tail(value)
    if len(value) > 140:
        sentence_match = re.match(r"^(.{45,140}?[.!?])\s+[A-Z0-9가-힣]", value)
        if sentence_match:
            value = sentence_match.group(1)
    value = normalize_whitespace(value)
    return value


def title_quality(title: str, source_slug: str | None = None) -> TitleQuality:
    raw = normalize_whitespace(title)
    cleaned = clean_title(raw, source_slug)
    reasons: list[str] = []
    if len(cleaned) > TITLE_SUSPECT_LENGTH:
        reasons.append("long_title")
    if "read more" in raw.casefold() and "read more" in cleaned.casefold():
        reasons.append("read_more_in_title")
    if has_body_start_pattern(cleaned):
        reasons.append("body_start_pattern")
    if not cleaned:
        reasons.append("empty_title")
    return TitleQuality(cleaned=cleaned, is_suspect=bool(reasons), reason=",".join(_dedupe_strings(reasons)))


def has_body_start_pattern(title: str) -> bool:
    return bool(BODY_START_ANYWHERE_RE.search(normalize_whitespace(title)))


def _strip_body_start_tail(value: str) -> str:
    clean = normalize_whitespace(value)
    if len(clean) < 24:
        return clean
    match = BODY_START_RE.search(clean)
    if not match or match.start() < 18:
        return clean
    return normalize_whitespace(clean[: match.start()])


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
    quality_fallback = summary_quality_fallback(
        trust_label=item.trust_label,
        content_type=getattr(item, "content_type", ""),
        title_suspect=getattr(item, "title_suspect", False),
    )
    if quality_fallback:
        return quality_fallback
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


def summary_quality_fallback(*, trust_label: str, content_type: str = "", title_suspect: bool = False) -> str:
    if title_suspect:
        if trust_label == TrustLabel.OFFICIAL:
            return "이 항목은 Nintendo 공식 페이지에서 수집된 소식이지만, 제목/본문 추출 품질 확인이 필요합니다. 원문 링크에서 세부 내용을 확인하세요."
        if trust_label == TrustLabel.RUMOR:
            return "이 항목은 루머/유출성 정보로, 공식 확인 전입니다. 현재 제목/본문 추출 품질 확인이 필요하므로 원문 링크에서 세부 내용을 확인하세요."
        return "이 항목은 해외 매체에서 수집된 기사입니다. 현재 상세 요약은 제한적이며, 원문 확인이 필요합니다."
    if content_type in STATIC_CONTENT_TYPES:
        return "이 항목은 뉴스 기사보다 목록/허브 성격이 강한 페이지입니다. 최신 핵심 뉴스로 보기 전에 원문 링크에서 맥락을 확인하세요."
    if trust_label == TrustLabel.RUMOR:
        return ""
    return ""


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


def classify_content_type(*, title: str, url: str, raw_text: str = "", source: Source | None = None) -> str:
    normalized = normalize_title(f"{title} {raw_text[:300]}")
    lowered_title = normalize_whitespace(title).casefold()
    path = (urlparse(url).path or "").casefold()
    if is_hub_url(url, source):
        return NewsContentType.HUB_PAGE
    if any(marker in path for marker in ["/all-games", "/games/", "/software/", "/platforms/"]):
        return NewsContentType.LIST_PAGE
    if any(marker in normalized for marker in ["all games", "software list", "switch games list", "games coming soon"]):
        return NewsContentType.LIST_PAGE
    if any(marker in normalized for marker in ["which nintendo switch is right for you", "support", "privacy", "terms"]):
        return NewsContentType.STATIC_PAGE
    if any(marker in lowered_title for marker in ["roundup", "breakdown", "rumour round-up", "rumor roundup", "summary", "everything announced"]):
        return NewsContentType.ROUNDUP
    if any(marker in path for marker in ["/guides/", "/guide/"]) or "guide" in normalized:
        return NewsContentType.GUIDE
    if any(marker in path for marker in ["/reviews/", "/review/"]) or normalized.startswith("review "):
        return NewsContentType.REVIEW
    if any(marker in normalized for marker in ["rumor", "rumour", "leak", "reportedly"]):
        return NewsContentType.RUMOR
    if source and source.trust_type == TrustType.OFFICIAL:
        return NewsContentType.OFFICIAL_NOTICE
    return NewsContentType.NEWS_ARTICLE


def is_low_quality_for_headline(item: NewsItem) -> bool:
    return bool(
        getattr(item, "title_suspect", False)
        or getattr(item, "is_date_suspect", False)
        or not getattr(item, "published_at", None)
        or getattr(item, "date_confidence", "") == DateConfidence.LOW
        or getattr(item, "content_type", "") in STATIC_CONTENT_TYPES
        or getattr(item, "is_backfill", False)
        or not getattr(item, "importance_reasons", None)
        or getattr(item, "nintendo_relevance_score", 0) < 3
    )


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


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
