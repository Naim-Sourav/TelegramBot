"""Prompt construction for the rewrite model."""

from __future__ import annotations

from .config import Settings
from .models import SourcePost

_LANGUAGE_NAMES = {
    "bn": "Bengali (বাংলা)",
    "en": "English",
    "hi": "Hindi",
    "ur": "Urdu",
    "ar": "Arabic",
}


def language_name(code: str) -> str:
    return _LANGUAGE_NAMES.get(code, code)


def build_system_prompt(settings: Settings) -> str:
    lang = language_name(settings.post_language)
    rules = [
        f"Write everything in {lang}. {settings.post_language.upper()} script only.",
        (
            "Understand the *idea* of the source post and then write it again from scratch. "
            "Never translate sentence by sentence, never copy more than a few unavoidable "
            "words (names, numbers, technical terms)."
        ),
        (
            "Do not mention, thank, link or hint at the original channel. No @mentions, "
            "no t.me links, no 'via', no 'source:' unless the input clearly contains an "
            "official primary source that readers need."
        ),
        "Do not invent facts, numbers, dates or quotes. If a detail is missing, leave it out.",
        "No clickbait, no ALL CAPS, no more than one emoji per few lines. Keep it clean.",
        "Headline: one strong line, max 90 characters, no trailing full stop.",
        f"Body: 2–5 short paragraphs, maximum {settings.max_post_chars} characters in total.",
        (
            f"Hashtags: exactly {settings.hashtag_count} short tags, no spaces, "
            "topical rather than generic (#breaking, #news are banned)."
        ),
        "Reading level: a general audience, easy and natural wording.",
        "Output JSON only.",
    ]
    if settings.style_rules:
        rules.extend(settings.style_rules)

    numbered = "\n".join(f"{i}. {rule}" for i, rule in enumerate(rules, start=1))
    return (
        f"You are the senior editor of the Telegram channel «{settings.channel_title}».\n"
        f"Channel voice and values: {settings.channel_persona}\n\n"
        "You receive one post from a competitor channel as raw material. "
        "Your job is to publish the same *idea* in our own voice, better organised and "
        "clearly rewritten — legally and editorially original.\n\n"
        f"Hard rules:\n{numbered}\n\n"
        "Return a JSON object with exactly these keys:\n"
        '  "headline": string,\n'
        '  "body": string (use \\n\\n between paragraphs, no headline repetition),\n'
        '  "hashtags": array of strings (without "#" is fine),\n'
        '  "alt_text": string (short description of a fitting image, may be empty)\n'
    )


def build_user_prompt(post: SourcePost, settings: Settings, hint: str = "") -> str:
    meta = [f"source_channel: {post.channel}", f"source_message_id: {post.message_id}"]
    if post.media_kind:
        meta.append(f"source_media: {post.media_kind}")
    text = post.full_text.strip()
    if len(text) > 6000:
        text = text[:6000] + "…"

    parts = [
        "SOURCE POST (raw material, do not copy its wording):",
        "<<<",
        text,
        ">>>",
        "",
        "Metadata: " + ", ".join(meta),
    ]
    if hint:
        parts += ["", "EDITOR NOTE: " + hint]
    parts += [
        "",
        f"Now write our version in {language_name(settings.post_language)} as JSON.",
    ]
    return "\n".join(parts)


def build_retry_hint(similarity: float, limit: float) -> str:
    return (
        f"The previous attempt was still {similarity:.0%} similar to the source text "
        f"(limit {limit:.0%}). Rewrite it again: change the structure, the opening line and "
        "the vocabulary completely while keeping the facts."
    )
