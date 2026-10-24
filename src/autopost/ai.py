"""AI rewrite layer.

Supports any OpenAI-compatible chat-completions endpoint (OpenAI, OpenRouter,
Groq, DeepSeek, local llama.cpp/Ollama, …) and Google Gemini.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx

from .config import Settings
from .models import Rewrite, SourcePost
from .prompts import build_retry_hint, build_system_prompt, build_user_prompt
from .textutil import clean_text, normalize_hashtags, similarity, truncate

log = logging.getLogger("autopost.ai")

JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class RewriteError(RuntimeError):
    """Raised when the model could not produce a usable rewrite."""


def extract_json(raw: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model response."""
    if not raw:
        raise RewriteError("empty model response")
    text = FENCE_RE.sub("", raw.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = JSON_BLOCK_RE.search(text)
        if not match:
            raise RewriteError(f"no JSON found in response: {raw[:200]!r}") from None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise RewriteError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RewriteError("model returned JSON that is not an object")
    return data


def parse_rewrite(data: dict[str, Any], settings: Settings) -> Rewrite:
    headline = clean_text(str(data.get("headline") or ""))
    body = clean_text(str(data.get("body") or data.get("text") or ""))
    hashtags = data.get("hashtags") or []
    if isinstance(hashtags, str):
        hashtags = [tag for tag in re.split(r"[\s,]+", hashtags) if tag]
    if not isinstance(hashtags, list):
        hashtags = []

    if not body and headline:
        headline, body = "", headline
    if not body:
        raise RewriteError("model returned an empty body")

    headline = truncate(headline, 120)
    body = truncate(body, settings.max_post_chars)
    return Rewrite(
        headline=headline,
        body=body,
        hashtags=normalize_hashtags([str(tag) for tag in hashtags], settings.hashtag_count),
        alt_text=clean_text(str(data.get("alt_text") or ""))[:280],
    )


class Rewriter:
    """Async wrapper around the configured model provider."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> Rewriter:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds)
            self._owns_client = True
        return self._client

    # ------------------------------------------------------------------ public
    async def rewrite(self, post: SourcePost) -> Rewrite:
        settings = self.settings
        if not settings.ai_api_key:
            raise RewriteError("AI_API_KEY is not configured")

        source = post.full_text
        hint = ""
        last_error: Exception | None = None
        best: Rewrite | None = None

        for attempt in range(1, max(1, settings.ai_max_retries) + 1):
            try:
                data = await self._chat(post, hint)
                candidate = parse_rewrite(data, settings)
            except (RewriteError, httpx.HTTPError) as exc:
                last_error = exc
                log.warning("rewrite attempt %s failed: %s", attempt, exc)
                await asyncio.sleep(min(2**attempt, 10))
                continue

            score = similarity(source, f"{candidate.headline} {candidate.body}")
            log.debug("attempt %s similarity %.2f", attempt, score)
            if best is None or score < similarity(source, f"{best.headline} {best.body}"):
                best = candidate
            if score <= settings.max_source_similarity:
                candidate.provider = settings.ai_provider
                candidate.model = settings.ai_model
                return candidate
            hint = build_retry_hint(score, settings.max_source_similarity)

        if best is not None and settings.allow_template_fallback:
            log.warning("using best-effort rewrite (similarity above limit)")
            best.provider = settings.ai_provider
            best.model = settings.ai_model
            return best
        raise RewriteError(f"could not produce an original rewrite: {last_error}")

    # --------------------------------------------------------------- providers
    async def _chat(self, post: SourcePost, hint: str) -> dict[str, Any]:
        system = build_system_prompt(self.settings)
        user = build_user_prompt(post, self.settings, hint)
        if self.settings.ai_provider == "gemini":
            return await self._gemini(system, user)
        return await self._openai(system, user)

    async def _openai(self, system: str, user: str) -> dict[str, Any]:
        settings = self.settings
        url = settings.ai_base_url.rstrip("/") + "/chat/completions"
        payload: dict[str, Any] = {
            "model": settings.ai_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": settings.ai_temperature,
        }
        payload_with_json = {**payload, "response_format": {"type": "json_object"}}

        response = await self.client.post(
            url,
            json=payload_with_json,
            headers={"Authorization": f"Bearer {settings.ai_api_key.get_secret_value()}"},
        )
        if response.status_code == 400 and "response_format" in response.text:
            # Some OpenAI-compatible servers reject json mode – fall back to plain.
            response = await self.client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {settings.ai_api_key.get_secret_value()}"},
            )
        if response.status_code >= 400:
            raise RewriteError(f"HTTP {response.status_code}: {response.text[:300]}")

        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RewriteError(f"unexpected response shape: {data}") from exc
        return extract_json(content or "")

    async def _gemini(self, system: str, user: str) -> dict[str, Any]:
        settings = self.settings
        base = settings.ai_base_url.rstrip("/")
        if "generativelanguage" not in base:
            base = "https://generativelanguage.googleapis.com/v1beta"
        url = f"{base}/models/{settings.ai_model}:generateContent"
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": settings.ai_temperature,
                "responseMimeType": "application/json",
            },
        }
        response = await self.client.post(
            url,
            json=payload,
            headers={"x-goog-api-key": settings.ai_api_key.get_secret_value()},
        )
        if response.status_code >= 400:
            raise RewriteError(f"HTTP {response.status_code}: {response.text[:300]}")

        data = response.json()
        try:
            parts = data["candidates"][0]["content"]["parts"]
            content = "".join(part.get("text", "") for part in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise RewriteError(f"unexpected response shape: {data}") from exc
        return extract_json(content or "")


def template_rewrite(post: SourcePost, settings: Settings) -> Rewrite:
    """Offline fallback: light clean-up + re-formatting, no generative AI.

    The headline is the first sentence, the body is everything after it, so the
    same sentence never appears twice.
    """
    from .textutil import strip_promotional

    cleaned = strip_promotional(post.full_text)
    lines = [line.strip() for line in cleaned.split("\n") if line.strip()]
    if not lines:
        return Rewrite(headline="", body="", provider="template", model="none")

    if len(lines) > 1:
        headline = truncate(lines[0], 110)
        body = "\n\n".join(lines[1:])
    else:
        sentences = re.split(r"(?<=[।.!?])\s+", lines[0], maxsplit=1)
        headline = truncate(sentences[0], 110)
        body = sentences[1] if len(sentences) > 1 else ""
        if not body:
            headline, body = "", lines[0]

    return Rewrite(
        headline=headline,
        body=truncate(body, settings.max_post_chars),
        hashtags=[],
        provider="template",
        model="none",
    )
