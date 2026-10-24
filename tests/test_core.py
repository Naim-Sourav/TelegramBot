"""Offline tests: no network, no Telegram credentials required."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from autopost.ai import RewriteError, extract_json, parse_rewrite, template_rewrite
from autopost.config import Settings
from autopost.db import Database
from autopost.pipeline import Pipeline
from autopost.prompts import build_system_prompt, build_user_prompt
from autopost.publisher import resolve_chat
from autopost.sources import normalize_ref
from autopost.textutil import (
    build_post_html,
    clean_text,
    content_hash,
    normalize_hashtags,
    overlap,
    similarity,
    strip_promotional,
    truncate,
)


def make_settings(**overrides) -> Settings:
    base = {
        "tg_api_id": 12345,
        "tg_api_hash": "abc",
        "bot_token": "123:abc",
        "target_channel": "@mychannel",
        "source_channels": ["@a", "@b"],
        "ai_api_key": "sk-test",
        "owner_user_ids": [111],
    }
    base.update(overrides)
    return Settings(**base)


# ------------------------------------------------------------------- textutil
def test_normalize_ref_accepts_common_forms():
    assert normalize_ref("@dailynews") == "@dailynews"
    assert normalize_ref("dailynews") == "@dailynews"
    assert normalize_ref("https://t.me/dailynews") == "@dailynews"
    assert normalize_ref("t.me/s/dailynews/") == "@dailynews"
    assert normalize_ref("-1001234567890") == "-1001234567890"


def test_normalize_ref_rejects_invite_links():
    with pytest.raises(ValueError):
        normalize_ref("https://t.me/+abcdef")


def test_resolve_chat_forms():
    assert resolve_chat("@chan") == "@chan"
    assert resolve_chat("chan") == "@chan"
    assert resolve_chat("https://t.me/chan") == "@chan"
    assert resolve_chat("-1001234567890") == -1001234567890


def test_strip_promotional_removes_channel_spam():
    text = (
        "বাংলাদেশের অর্থনীতি ঘুরে দাঁড়াচ্ছে\n"
        "রপ্তানি আয় বেড়েছে ১২ শতাংশ\n"
        "Join our channel @spamchannel\n"
        "https://t.me/spamchannel\n"
        "সাবস্ক্রাইব করুন"
    )
    cleaned = strip_promotional(text)
    assert "spamchannel" not in cleaned
    assert "রপ্তানি" in cleaned


def test_similarity_detects_copy_bengali():
    a = "আজ ঢাকায় ভারী বৃষ্টির সম্ভাবনা রয়েছে, সন্ধ্যা পর্যন্ত চলতে পারে।"
    b = "আজ ঢাকায় ভারী বৃষ্টির সম্ভাবনা রয়েছে, সন্ধ্যা পর্যন্ত চলতে পারে।"
    c = "আগামীকাল চট্টগ্রামে তাপমাত্রা বাড়তে পারে বলে আবহাওয়া অফিস জানিয়েছে।"
    assert similarity(a, b) > 0.9
    assert similarity(a, c) < 0.3


def test_overlap_spots_repost_with_extra_text():
    body = "ইউরোপের কয়েকটি দেশে ফের লকডাউন জল্পনা। স্বাস্থ্য কর্মকর্তারা বলছেন সংক্রমণ বাড়ছে।"
    repost = body + " বিস্তারিত জানতে চ্যানেলে চোখ রাখুন।"
    different = "কক্সবাজারে নতুন পর্যটন কেন্দ্র উদ্বোধন করা হয়েছে আজ সকালে।"
    assert overlap(body, repost) > 0.8
    assert overlap(body, different) < 0.4


def test_content_hash_ignores_whitespace_and_case():
    assert content_hash("Hello   World") == content_hash("hello world")


def test_truncate_keeps_limit_and_boundaries():
    text = "প্রথম বাক্য। দ্বিতীয় বাক্য। তৃতীয় বাক্য যা অনেক লম্বা এবং শেষে কাটা যাবে।"
    out = truncate(text, 30)
    assert len(out) <= 30
    assert out.endswith("…")


def test_normalize_hashtags_limits_and_cleans():
    tags = normalize_hashtags(["#বাংলা", "বাংলা", "#tech news", "##tech", "#a", ""], 3)
    assert tags[0] == "#বাংলা"
    assert len(tags) <= 2
    assert all(tag.startswith("#") and " " not in tag for tag in tags)


def test_build_post_html_escapes_and_orders():
    html = build_post_html("শিরোনাম <b>", "বডি & more", ["#tags"], "📌 @chan", has_media=False)
    assert html.startswith("<b>শিরোনাম &lt;b&gt;</b>")
    assert "&amp;" in html
    assert html.endswith("📌 @chan")


def test_clean_text_collapses_blank_lines():
    assert clean_text("a\n\n\n\nb") == "a\n\nb"


# ------------------------------------------------------------------------- ai
def test_extract_json_from_fenced_block():
    raw = '```json\n{"headline": "x", "body": "y"}\n```'
    assert extract_json(raw)["headline"] == "x"


def test_extract_json_with_chatter():
    raw = 'Here you go: {"headline": "a", "body": "b"} hope it helps'
    assert extract_json(raw)["body"] == "b"


def test_extract_json_raises_on_garbage():
    with pytest.raises(RewriteError):
        extract_json("no json here")


def test_parse_rewrite_normalises_fields():
    settings = make_settings(hashtag_count=2, max_post_chars=50)
    rewrite = parse_rewrite(
        {"headline": "শিরোনাম", "body": "একটি দীর্ঘ বডি " * 10, "hashtags": "বাংলাদেশ, খবর"},
        settings,
    )
    assert rewrite.headline == "শিরোনাম"
    assert len(rewrite.body) <= 50
    assert rewrite.hashtags == ["#বাংলাদেশ", "#খবর"]


def test_parse_rewrite_moves_lonely_headline_into_body():
    rewrite = parse_rewrite({"headline": "only a headline"}, make_settings())
    assert rewrite.headline == ""
    assert rewrite.body == "only a headline"


def test_parse_rewrite_rejects_empty_payload():
    with pytest.raises(RewriteError):
        parse_rewrite({}, make_settings())


def test_template_fallback_works_without_ai():
    from autopost.models import SourcePost

    settings = make_settings()
    post = SourcePost(
        channel="src",
        message_id=1,
        text="প্রথম লাইন শিরোনাম\n\nদ্বিতীয় অংশ বিস্তারিত লেখা।",
        date=datetime.now(timezone.utc),
    )
    rewrite = template_rewrite(post, settings)
    assert rewrite.headline == "প্রথম লাইন শিরোনাম"
    assert rewrite.body == "দ্বিতীয় অংশ বিস্তারিত লেখা।"
    assert rewrite.provider == "template"


def test_template_fallback_does_not_repeat_single_sentence():
    from autopost.models import SourcePost

    settings = make_settings()
    one = "একটাই বাক্য এবং সেটাই পুরো পোস্টের সবটুকু লেখা।"
    post = SourcePost("src", 2, one, datetime.now(timezone.utc))
    rewrite = template_rewrite(post, settings)
    assert rewrite.headline == ""
    assert rewrite.body == one


def test_prompts_mention_language_and_rules():
    settings = make_settings(post_language="bn")
    system = build_system_prompt(settings)
    assert "Bengali" in system
    assert "JSON" in system
    from autopost.models import SourcePost

    post = SourcePost("src", 7, "some source text", datetime.now(timezone.utc))
    user = build_user_prompt(post, settings)
    assert "some source text" in user
    assert "source_message_id: 7" in user


# ------------------------------------------------------------------------ db
async def test_database_dedup_and_drafts(tmp_path):
    async with Database(tmp_path / "t.sqlite3") as db:
        assert not await db.is_seen("chan", 1)
        await db.mark_seen("chan", 1, content_hash("hello world"))
        assert await db.is_seen("chan", 1)
        assert await db.hash_seen_recently(content_hash("hello world"), hours=24)

        draft_id = await db.create_draft(
            source_channel="chan",
            source_message_id=1,
            text="<b>x</b>",
            content_hash="h",
        )
        row = await db.get_draft(draft_id)
        assert row is not None and row["status"] == "pending"

        await db.update_draft(draft_id, status="approved")
        row = await db.get_draft(draft_id)
        assert row["status"] == "approved"

        await db.record_publish(
            draft_id=draft_id,
            target_message_id=9,
            content_hash="h",
            publish_date="2026-01-01",
        )
        assert await db.count_published_on("2026-01-01") == 1
        assert await db.count_published_on("2026-01-02") == 0

        assert not await db.is_paused()
        await db.set_paused(True)
        assert await db.is_paused()


# ------------------------------------------------------------------ pipeline
class FakeRewriter:
    def __init__(self, body: str = "সম্পূর্ণ নতুন লেখা।"):
        self.body = body
        self.calls = 0

    async def rewrite(self, post):  # type: ignore[no-untyped-def]
        from autopost.models import Rewrite

        self.calls += 1
        return Rewrite(headline="নতুন শিরোনাম", body=self.body, hashtags=["#খবর"])


class FailingRewriter:
    async def rewrite(self, post):  # type: ignore[no-untyped-def]
        raise RewriteError("boom")


def make_post(text: str = "একটি দীর্ঘ সোর্স পোস্ট যাতে যথেষ্ট লেখা আছে। " * 5, **kwargs):  # type: ignore[no-untyped-def]
    from autopost.models import SourcePost

    return SourcePost(
        channel="src",
        message_id=42,
        text=text,
        date=datetime.now(timezone.utc),
        permalink="https://t.me/src/42",
        **kwargs,
    )


async def test_pipeline_queues_for_review(tmp_path):
    settings = make_settings(auto_publish=False, min_source_chars=10)
    notified: list[tuple] = []

    async def notifier(*args):  # type: ignore[no-untyped-def]
        notified.append(args)

    async with Database(tmp_path / "p.sqlite3") as db:
        pipeline = Pipeline(
            settings, db, rewriter=FakeRewriter(), notifier=notifier  # type: ignore[arg-type]
        )
        outcome = await pipeline.process(make_post())
        assert outcome == "queued"
        assert notified and notified[0][0] == 1
        rows = await db.list_drafts(status="pending")
        assert len(rows) == 1
        assert "<b>নতুন শিরোনাম</b>" in rows[0]["text"]
        assert await db.is_seen("src", 42)


async def test_pipeline_auto_publish_calls_publisher(tmp_path):
    settings = make_settings(auto_publish=True, min_source_chars=10)

    class FakePublisher:
        def __init__(self):
            self.sent: list[tuple] = []

        async def publish(self, text, media_kind=None, media_path=None):  # type: ignore[no-untyped-def]
            self.sent.append((text, media_kind, media_path))
            return 777

    publisher = FakePublisher()
    async with Database(tmp_path / "a.sqlite3") as db:
        pipeline = Pipeline(
            settings, db, rewriter=FakeRewriter(), publisher=publisher  # type: ignore[arg-type]
        )
        assert await pipeline.process(make_post()) == "published"
        assert publisher.sent[0][0].startswith("<b>")
        assert await db.count_published_on(pipeline.local_now().date().isoformat()) == 1


async def test_pipeline_handles_ai_failure(tmp_path):
    settings = make_settings(min_source_chars=10)
    async with Database(tmp_path / "f.sqlite3") as db:
        pipeline = Pipeline(settings, db, rewriter=FailingRewriter())  # type: ignore[arg-type]
        assert await pipeline.process(make_post()) == "error"


async def test_reject_reason_filters_short_and_keywords(tmp_path):
    settings = make_settings(min_source_chars=50, blocked_keywords=["জুয়া"])
    async with Database(tmp_path / "r.sqlite3") as db:
        pipeline = Pipeline(settings, db, rewriter=FakeRewriter())  # type: ignore[arg-type]
        assert "too short" in (pipeline.reject_reason(make_post("ছোট লেখা")) or "")
        long_bad = ("জুয়া খেলার আমন্ত্রণ " * 10).strip()
        assert "blocked keyword" in (pipeline.reject_reason(make_post(long_bad)) or "")
        assert pipeline.reject_reason(make_post()) is None


async def test_active_hours_window(tmp_path):
    settings = make_settings(active_hours_start=9, active_hours_end=23)
    async with Database(tmp_path / "h.sqlite3") as db:
        pipeline = Pipeline(settings, db, rewriter=FakeRewriter())  # type: ignore[arg-type]
        pipeline.local_now = lambda: datetime(2026, 1, 1, 12, tzinfo=timezone.utc)  # type: ignore[assignment]
        assert pipeline.within_active_hours() is True
        pipeline.local_now = lambda: datetime(2026, 1, 1, 2, tzinfo=timezone.utc)  # type: ignore[assignment]
        assert pipeline.within_active_hours() is False


async def test_compose_trims_caption_for_media(tmp_path):
    settings = make_settings(max_post_chars=2000, signature="📌 @chan", min_source_chars=10)
    async with Database(tmp_path / "c.sqlite3") as db:
        pipeline = Pipeline(settings, db, rewriter=FakeRewriter("বডি " * 800))  # type: ignore[arg-type]
        html = pipeline.compose(make_post(media_kind="photo"), await FakeRewriter().rewrite(None))  # type: ignore[arg-type]
        assert len(html) <= 1100


# -------------------------------------------------------------------- config
def test_config_csv_parsing():
    settings = Settings(
        source_channels="@a, @b\n@c",  # type: ignore[arg-type]
        owner_user_ids="1,2",  # type: ignore[arg-type]
        blocked_keywords=" casino , জুয়া ",
    )
    assert settings.source_channels == ["@a", "@b", "@c"]
    assert settings.owner_user_ids == [1, 2]
    assert settings.blocked_keywords == ["casino", "জুয়া"]


def test_settings_helpers():
    settings = make_settings()
    assert settings.missing_for_ai() == []
    assert settings.missing_for_write() == []
    assert settings.review_target() == 111
    assert settings.db_path.name == "autopost.sqlite3"
