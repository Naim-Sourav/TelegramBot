"""Text helpers: cleaning, similarity, caption building, HTML escaping."""

from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import urlparse

URL_RE = re.compile(r"https?://\S+|www\.\S+|t\.me/\S+", re.IGNORECASE)
MENTION_RE = re.compile(r"@[A-Za-z0-9_]{4,}")
HASHTAG_RE = re.compile(r"#[\w\u0980-\u09FF]+", re.UNICODE)
SPACE_RE = re.compile(r"[ \t\u00a0]+")
MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
JOIN_CHANNEL_HINTS = (
    "join",
    "subscribe",
    "সাবস্ক্রাইব",
    "জয়েন",
    "যোগ দিন",
    "চ্যানেলে",
    "forwarded from",
    "via @",
)

ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)


def clean_text(text: str) -> str:
    """Normalise whitespace and drop control characters."""
    if not text:
        return ""
    text = text.translate(ZERO_WIDTH)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = SPACE_RE.sub(" ", text)
    text = MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


def strip_promotional(text: str) -> str:
    """Remove channel promo lines and foreign links/mentions from source text."""
    kept: list[str] = []
    for line in text.split("\n"):
        low = line.lower().strip()
        if not low:
            kept.append("")
            continue
        if MENTION_RE.fullmatch(low) or URL_RE.fullmatch(low):
            continue
        if any(hint in low for hint in JOIN_CHANNEL_HINTS) and len(low) < 120:
            continue
        kept.append(line)
    result = "\n".join(kept)
    result = MENTION_RE.sub("", result)
    result = URL_RE.sub("", result)
    return clean_text(result)


def content_hash(text: str) -> str:
    """Stable fingerprint used to dedupe the same news across channels."""
    tokens = re.findall(r"\w+", (text or "").lower())
    return hashlib.sha1(" ".join(tokens).encode("utf-8")).hexdigest()


def _trigrams(text: str) -> set[str]:
    squashed = re.sub(r"\s+", " ", (text or "").lower())
    if len(squashed) < 3:
        return {squashed} if squashed else set()
    return {squashed[i : i + 3] for i in range(len(squashed) - 2)}


def similarity(a: str, b: str) -> float:
    """Character-trigram Jaccard similarity, works for Bengali as well as English."""
    sa, sb = _trigrams(a), _trigrams(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def overlap(a: str, b: str) -> float:
    """Containment score: how much of the *shorter* text also appears in the other.

    Better than Jaccard for spotting the same story reposted with extra intro/outro.
    """
    sa, sb = _trigrams(a), _trigrams(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def words(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def truncate(text: str, limit: int) -> str:
    """Cut at a sentence/word boundary so the result stays under `limit`."""
    text = clean_text(text)
    if limit <= 0 or len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    window = text[: limit - 1]
    cut = max(window.rfind("। "), window.rfind(". "), window.rfind("\n\n"))
    if cut < limit * 0.5:
        cut = max(window.rfind(" "), window.rfind("।"), window.rfind("."))
    if cut < limit * 0.5:
        cut = limit - 1
    return window[:cut].rstrip(" ,;।-–") + "…"


def normalize_hashtags(tags: list[str], limit: int) -> list[str]:
    """Keep hashtags unique, well-formed and limited in number."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in tags or []:
        tag = (raw or "").strip().replace(" ", "_")
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = "#" + tag.lstrip("#")
        tag = HASHTAG_RE.sub("", tag) if not HASHTAG_RE.fullmatch(tag) else tag
        tag = tag[:40]
        if len(tag) < 3:  # a single letter tag is noise
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= limit:
            break
    return out


def escape(text: str) -> str:
    return html.escape(text or "", quote=False)


def build_post_html(
    headline: str,
    body: str,
    hashtags: list[str] | None = None,
    signature: str = "",
    has_media: bool = False,
    caption_limit: int = 1024,
) -> str:
    """Render the final Telegram HTML payload with safe escaping."""
    parts: list[str] = []
    if headline.strip():
        parts.append(f"<b>{escape(headline.strip())}</b>")
    if body.strip():
        parts.append(escape(body.strip()))
    if hashtags:
        parts.append(escape(" ".join(hashtags)))
    if signature.strip():
        parts.append(escape(signature.strip()))

    text = "\n\n".join(parts)
    if has_media:
        text = truncate_html(text, caption_limit)
    return text


def truncate_html(text: str, limit: int) -> str:
    """Trim an HTML payload without breaking tags (only <b> is produced here)."""
    if len(text) <= limit:
        return text
    # Rebuild as plain text and re-apply bold on the first line to stay safe.
    plain = html.unescape(re.sub(r"<[^>]+>", "", text))
    plain = truncate(plain, limit)
    lines = plain.split("\n\n", 1)
    if len(lines) == 2:
        return f"<b>{escape(lines[0])}</b>\n\n{escape(lines[1])}"
    return escape(plain)


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc
    except ValueError:
        return ""
