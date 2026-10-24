"""Tests for the AI client (with a mocked HTTP transport) and the bot wiring."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from autopost.ai import RewriteError, Rewriter
from autopost.bot import ReviewBot, review_keyboard
from autopost.config import Settings
from autopost.db import Database
from autopost.models import SourcePost
from autopost.pipeline import Pipeline


def settings(**overrides) -> Settings:
    base = {
        "tg_api_id": 1,
        "tg_api_hash": "h",
        "bot_token": "123456:AAHfake_token_for_tests",
        "target_channel": "@mychannel",
        "source_channels": ["@a"],
        "ai_api_key": "sk-test",
        "owner_user_ids": [5],
        "ai_max_retries": 2,
        "max_source_similarity": 0.45,
    }
    base.update(overrides)
    return Settings(**base)


def source_post(text: str = "Artificial intelligence is changing how newsrooms work today."):
    return SourcePost("src", 1, text, datetime.now(timezone.utc))


def openai_response(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]},
    )


async def test_openai_provider_returns_rewrite():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return openai_response(
            {"headline": "নতুন যুগ", "body": "সম্পূর্ণ ভিন্নভাবে লেখা হয়েছে।", "hashtags": ["#এআই"]}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with Rewriter(settings(), client=client) as rewriter:
        result = await rewriter.rewrite(source_post())
    assert result.headline == "নতুন যুগ"
    assert result.hashtags == ["#এআই"]
    assert captured[0].headers["authorization"] == "Bearer sk-test"
    body = json.loads(captured[0].content)
    assert body["response_format"] == {"type": "json_object"}
    await client.aclose()


async def test_retry_when_rewrite_is_too_similar():
    replies = iter(
        [
            # first attempt: verbatim copy -> rejected
            {
                "headline": "",
                "body": "Artificial intelligence is changing how newsrooms work today.",
                "hashtags": [],
            },
            # second attempt: genuinely rewritten
            {
                "headline": "সংবাদমাধ্যমে কৃত্রিম বুদ্ধিমত্তা",
                "body": "নিউজরুমের কাজের ধরন বদলে দিচ্ছে এআই প্রযুক্তি।",
                "hashtags": ["#এআই"],
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return openai_response(next(replies))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with Rewriter(settings(), client=client) as rewriter:
        result = await rewriter.rewrite(source_post())
    assert "এআই" in result.body
    await client.aclose()


async def test_http_error_is_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad key")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with Rewriter(settings(ai_max_retries=1), client=client) as rewriter:
        with pytest.raises(RewriteError):
            await rewriter.rewrite(source_post())
    await client.aclose()


async def test_gemini_provider():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(":generateContent")
        assert request.headers["x-goog-api-key"] == "sk-test"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {"headline": "শিরোনাম", "body": "বডি লেখা", "hashtags": []}
                                    )
                                }
                            ]
                        }
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = settings(ai_provider="gemini", ai_model="gemini-2.0-flash")
    async with Rewriter(cfg, client=client) as rewriter:
        result = await rewriter.rewrite(source_post())
    assert result.body == "বডি লেখা"
    await client.aclose()


async def test_bot_wiring_and_permissions(tmp_path):
    cfg = settings()
    async with Database(tmp_path / "bot.sqlite3") as db:
        pipeline = Pipeline(cfg, db)
        bot = ReviewBot(cfg, db, pipeline)
        assert pipeline.notifier is not None
        assert bot.is_owner(5) and not bot.is_owner(6)
        assert len(bot.router.message.handlers) >= 10
        assert len(bot.router.callback_query.handlers) == 4
        assert review_keyboard(7).inline_keyboard[0][0].callback_data == "publish:7"
        await bot.bot.session.close()


async def test_bot_open_when_no_owner_ids(tmp_path):
    cfg = settings(owner_user_ids=[])
    async with Database(tmp_path / "open.sqlite3") as db:
        bot = ReviewBot(cfg, db, Pipeline(cfg, db))
        assert bot.is_owner(999)
        await bot.bot.session.close()
