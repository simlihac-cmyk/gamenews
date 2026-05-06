from __future__ import annotations

from copy import deepcopy
from typing import Any

from news.models import Source


COMMON_REJECT_URL_PATTERNS = [
    "/category/",
    "/tag/",
    "/tags/",
    "/platforms/",
    "/search",
    "/page/",
    "/news/rss",
    "/features/rss",
    "/reviews/rss",
    "/tips-and-tricks",
    "/nintendo-direct",
    "/creators-voice",
    "/all-games",
]

COMMON_TITLE_EXCLUDE_EXACT = [
    "News RSS",
    "Features RSS",
    "Reviews RSS",
    "RSS Feed",
    "Tips and tricks",
    "Creator's Voice",
    "Nintendo Direct",
    "All Nintendo Switch games",
    "All games",
]


SOURCE_ADAPTERS: dict[str, dict[str, Any]] = {
    "nintendo-kr-news": {
        "timezone": "Asia/Seoul",
        "language": "ko",
        "region": "KR",
        "reject_url_patterns": ["/kr/search", "/kr/news/rss", "/kr/software/"],
    },
    "nintendo-kr-schedule": {
        "timezone": "Asia/Seoul",
        "language": "ko",
        "region": "KR",
        "reject_url_patterns": ["/kr/search", "/kr/news/rss"],
        "quality_allow_title_patterns": [r".+"],
    },
    "nintendo-us-whats-new": {
        "timezone": "America/Los_Angeles",
        "language": "en",
        "region": "US",
        "reject_url_patterns": ["/us/search", "/us/store", "/us/games", "/us/switch", "/us/hardware", "/us/gaming-systems", "/news/rss"],
    },
    "nintendo-uk-news": {
        "timezone": "Europe/London",
        "language": "en",
        "region": "EU",
        "reject_url_patterns": ["/Hardware/", "/Games/", "/Support/", "/Search/", "/Nintendo-eShop/", "/Nintendo-Switch-2/", "/Nintendo-Switch/", "/news/rss"],
    },
    "nintendo-youtube-kr": {
        "timezone": "Asia/Seoul",
        "language": "ko",
        "region": "KR",
        "reject_url_patterns": [],
    },
    "vgc-nintendo": {
        "timezone": "Europe/London",
        "language": "en",
        "region": "GLOBAL",
        "reject_url_patterns": [
            "/platforms/",
            "/category/",
            "/tag/",
            "/news/rss",
            "/features/rss",
            "/reviews/rss",
            "/page/",
        ],
    },
    "nintendo-life": {
        "timezone": "Europe/London",
        "language": "en",
        "region": "GLOBAL",
        "reject_url_patterns": ["/guides/", "/forums/", "/games/", "/news/rss", "/feeds/"],
    },
    "gematsu": {
        "timezone": "UTC",
        "language": "en",
        "region": "GLOBAL",
        "reject_url_patterns": ["/category/", "/tag/", "/platform/", "/games/", "/feed"],
    },
    "gaming-leaks-rumours-reddit": {
        "timezone": "UTC",
        "language": "en",
        "region": "GLOBAL",
        "reject_url_patterns": ["/search", "/comments/?", "/r/GamingLeaksAndRumours/wiki"],
    },
}


def adapter_config(source: Source | None) -> dict[str, Any]:
    if source is None:
        return {
            "reject_url_patterns": list(COMMON_REJECT_URL_PATTERNS),
            "title_exclude_exact": list(COMMON_TITLE_EXCLUDE_EXACT),
        }

    merged: dict[str, Any] = deepcopy(SOURCE_ADAPTERS.get(source.slug, {}))
    configured = deepcopy(source.config or {})
    merged.update(configured)
    merged["reject_url_patterns"] = _dedupe_strings(
        [*COMMON_REJECT_URL_PATTERNS, *merged.get("reject_url_patterns", []), *configured.get("url_exclude_patterns", [])]
    )
    merged["url_exclude_patterns"] = _dedupe_strings(
        [*COMMON_REJECT_URL_PATTERNS, *merged.get("url_exclude_patterns", []), *merged.get("reject_url_patterns", [])]
    )
    merged["title_exclude_exact"] = _dedupe_strings(
        [*COMMON_TITLE_EXCLUDE_EXACT, *merged.get("title_exclude_exact", [])]
    )
    return merged


def _dedupe_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result
