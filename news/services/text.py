from __future__ import annotations

import hashlib
import re
import unicodedata
from html import unescape
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup


TRACKING_QUERY_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
}

TOKEN_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "new",
    "news",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "about",
    "this",
    "that",
    "update",
    "nintendo",
    "switch",
    "닌텐도",
}


def normalize_url(url: str, base_url: str | None = None) -> str:
    """Normalize URL enough for dedupe without trying to canonicalize the web."""
    if not url:
        return ""

    joined = urljoin(base_url, url.strip()) if base_url else url.strip()
    parsed = urlparse(joined)
    if not parsed.scheme or not parsed.netloc:
        return joined

    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    query_pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower in TRACKING_QUERY_PARAMS or key_lower.startswith("utm_"):
            continue
        query_pairs.append((key, value))
    query = urlencode(sorted(query_pairs), doseq=True)

    return urlunparse((scheme, netloc, path, "", query, ""))


def normalize_title(title: str) -> str:
    if not title:
        return ""
    value = unicodedata.normalize("NFKC", unescape(title)).lower()
    value = value.replace("’", "'").replace("“", '"').replace("”", '"')
    value = re.sub(r"\bnintendo\s+switch\s*2\b", "switch2", value)
    value = re.sub(r"\bswitch\s*2\b", "switch2", value)
    value = value.replace("switch ２", "switch2").replace("switch２", "switch2")
    value = re.sub(r"[_\-–—/:|·•]+", " ", value)
    value = re.sub(r"[^\w\s가-힣ぁ-ゟ゠-ヿ一-龯]", " ", value, flags=re.UNICODE)
    value = value.replace("_", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def create_content_hash(title: str, canonical_url: str = "") -> str:
    payload = f"{normalize_title(title)}|{canonical_url}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def create_url_hash(url: str) -> str:
    if not url:
        return ""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def strip_html(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return normalize_whitespace(soup.get_text(" "))


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def contains_hangul(value: str) -> bool:
    return bool(re.search(r"[가-힣]", value or ""))


def contains_japanese(value: str) -> bool:
    return bool(re.search(r"[ぁ-ゟ゠-ヿ一-龯]", value or ""))


def extract_sentences(text: str, limit: int = 3) -> list[str]:
    clean = normalize_whitespace(text)
    if not clean:
        return []
    pieces = re.split(r"(?<=[.!?。！？])\s+|\n+", clean)
    sentences = [piece.strip() for piece in pieces if len(piece.strip()) >= 8]
    if not sentences and clean:
        sentences = [clean[:240].strip()]
    return sentences[:limit]


def tokenize_topic(value: str) -> set[str]:
    normalized = normalize_title(value)
    tokens = {token for token in normalized.split() if len(token) >= 2 and token not in TOKEN_STOPWORDS}
    return tokens


def canonical_topic_from_title(title: str, max_tokens: int = 12) -> str:
    tokens = list(tokenize_topic(title))
    if not tokens:
        return normalize_title(title)[:500]
    return " ".join(sorted(tokens)[:max_tokens])[:500]


def truncate(value: str, length: int = 500) -> str:
    clean = normalize_whitespace(value)
    if len(clean) <= length:
        return clean
    return clean[: length - 1].rstrip() + "…"
