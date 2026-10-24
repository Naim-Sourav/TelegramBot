"""`autopost demo` — run the whole pipeline offline with fake fixtures.

No Telegram account, no API key and no network needed. Useful to see exactly
what the bot would produce before you wire up real credentials.
"""

from __future__ import annotations

import asyncio
import html
import logging
import tempfile
from datetime import timedelta
from pathlib import Path

from .ai import Rewriter, template_rewrite
from .config import Settings
from .db import Database, utcnow
from .models import Rewrite, SourcePost
from .pipeline import Pipeline
from .textutil import normalize_hashtags

log = logging.getLogger("autopost.demo")

FIXTURES: list[dict] = [
    {
        "channel": "technews_bd",
        "message_id": 8111,
        "media_kind": "photo",
        "text": (
            "OpenAI নতুন মডেল উন্মুক্ত করল, দাবি ৪০% কম খরচে সমান পারফরম্যান্স। "
            "কোম্পানির ব্লগ পোস্টে বলা হয়েছে নতুন মডেলটি আগের সংস্করণের চেয়ে দ্রুত এবং "
            "কম্পিউটার ব্যবহারে বেশি সাশ্রয়ী। ডেভেলপাররা আজ থেকেই API-তে এটি ব্যবহার করতে পারবেন। "
            "দাম কমলেও কোয়ালিটিতে কোনো ছাড় নেই বলে কোম্পানি দাবি করছে।"
        ),
    },
    {
        "channel": "technews_bd",
        "message_id": 8112,
        "text": (
            "বাংলাদেশে ইন্টারনেট ব্যবহারকারীর সংখ্যা ১৩ কোটি ছাড়িয়েছে বলে জানিয়েছে "
            "বিটিআরসি। সংস্থার সর্বশেষ প্রতিবেদনে বলা হয়েছে, মোবাইল ইন্টারনেট গ্রাহক "
            "বেড়েছে ৮ শতাংশ। তবে গতির ক্ষেত্রে দেশ এখনো দক্ষিণ এশিয়ার অন্যান্য দেশের চেয়ে পিছিয়ে।"
        ),
    },
    {
        "channel": "world_digest",
        "message_id": 4432,
        "text": (
            "ইউরোপের কয়েকটি দেশে ফের লকডাউন জল্পনা। স্বাস্থ্য কর্মকর্তারা বলছেন, "
            "শীতের শুরুতে সংক্রমণ বাড়ছে, তাই মাস্ক ও টিকা নিয়ে নতুন নির্দেশনা আসতে পারে। "
            "স্থানীয় সংবাদমাধ্যম বলছে সিদ্ধান্ত হবে আগামী সপ্তাহে।"
        ),
    },
    {
        "channel": "world_digest",
        "message_id": 4433,
        "text": "খুব ছোট একটা পোস্ট যা ফিল্টার হওয়া উচিত।",
    },
    {
        "channel": "world_digest",
        "message_id": 4434,
        "text": (
            "ইউরোপের কয়েকটি দেশে ফের লকডাউন জল্পনা। স্বাস্থ্য কর্মকর্তারা বলছেন, শীতের "
            "শুরুতে সংক্রমণ বাড়ছে, তাই মাস্ক ও টিকা নিয়ে নতুন নির্দেশনা আসতে পারে "
            "— এটা একই খবর, তাই ডুপ্লিকেট হিসেবে বাদ যাবে।"
        ),
    },
    {
        "channel": "technews_bd",
        "message_id": 8113,
        "media_kind": "video",
        "text": (
            "মোবাইল ফোনে ব্যাটারি লাইফ বাড়ানোর সহজ উপায়গুলো নিয়ে একটি নতুন পর্যালোচনা প্রকাশ "
            "হয়েছে। সেটিংসে কিছু সহজ পরিবর্তন করলেই ব্যাটারির আয়ু অনেক বাড়ে বলে পর্যালোচনায় "
            "দাবি করা হয়েছে। বিশেষজ্ঞরা বলছেন, তেমন দাবির আগে যন্ত্রভিত্তিক পরীক্ষা দরকার।"
        ),
    },
]


class DemoReader:
    """Pretends to be the Telethon userbot and returns fixtures."""

    def __init__(self, settings: Settings, used: set[tuple[str, int]] | None = None) -> None:
        self.settings = settings
        self.used = used if used is not None else set()

    async def connect(self) -> DemoReader:
        return self

    async def disconnect(self) -> None:
        return None

    async def fetch(self, channel: str, **kwargs: object) -> list[SourcePost]:
        now = utcnow()
        posts = []
        for index, item in enumerate(FIXTURES):
            if item["channel"] != channel.lstrip("@"):
                continue
            key = (item["channel"], int(item["message_id"]))
            if key in self.used:
                continue
            posts.append(
                SourcePost(
                    channel=item["channel"],
                    message_id=int(item["message_id"]),
                    text=str(item["text"]),
                    date=now - timedelta(minutes=5 + index * 3),
                    media_kind=item.get("media_kind"),
                    permalink=f"https://t.me/{item['channel']}/{item['message_id']}",
                    views=900 - index * 40,
                    forwards=20 - index,
                )
            )
        return posts


class DemoPublisher:
    """Records posts instead of calling the Bot API."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def publish(self, text, media_kind=None, media_path=None, **kwargs: object) -> int:  # type: ignore[no-untyped-def]
        message_id = 1000 + len(self.sent) + 1
        self.sent.append(
            {"text": text, "media_kind": media_kind, "message_id": message_id}
        )
        return message_id


class DemoRewriter:
    """Deterministic stand-in for the LLM so the demo never needs an API key."""

    provider = "demo"
    model = "offline"

    async def rewrite(self, post: SourcePost) -> Rewrite:
        await asyncio.sleep(0)
        base = template_rewrite(post, Settings())
        tags_by_channel = {
            "technews_bd": ["প্রযুক্তি", "টেকনোলজি", "বাংলাদেশ"],
            "world_digest": ["বিশ্ব", "আন্তর্জাতিক", "খবর"],
        }
        return Rewrite(
            headline=(base.headline or "ডেমো শিরোনাম").rstrip("।."),
            body=base.body or base.headline,
            hashtags=normalize_hashtags(tags_by_channel.get(post.channel, ["খবর"]), 3),
            provider=self.provider,
            model=self.model,
        )


def _demo_settings(data_dir: Path) -> Settings:
    return Settings(
        target_channel="@demo_channel",
        source_channels=["@technews_bd", "@world_digest"],
        owner_user_ids=[1],
        bot_token="0:demo",
        ai_api_key="demo",
        min_source_chars=80,
        max_posts_per_run=10,
        daily_post_limit=20,
        auto_publish=False,
        media_enabled=True,
        publish_delay_seconds=0,
        data_dir=data_dir,
        dedupe_window_hours=24,
        signature="📌 @demo_channel",
    )


def _render_html(rows: list[dict], summary_lines: list[str], live: bool) -> str:
    live_note = (
        '<br><b style="color:#ffd479">লাইভ AI ব্যবহার হয়েছে</b>'
        if live
        else "<br>AI ছাড়া ডেমো রিরাইটার ব্যবহার হয়েছে (আসল বটে LLM বসবে)"
    )
    bubble_html = []
    for row in rows:
        media_badge = {
            "photo": "🖼 ছবি সহ",
            "video": "🎬 ভিডিও সহ",
            "gif": "🎞 GIF সহ",
        }.get(row.get("media_kind") or "", "📝 শুধু টেক্সট")
        bubble_html.append(
            f"""
      <article class="post">
        <div class="meta">{html.escape(row['source'])} → {media_badge}</div>
        <div class="bubble">{row['text']}</div>
        <div class="actions">✅ পাবলিশ · 🔄 রিরাইট · ✏️ এডিট · ❌ বাতিল</div>
      </article>"""
        )

    return f"""<!doctype html>
<html lang="bn">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>অটোপোস্ট ডেমো</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px 16px 64px;
    font-family: "Noto Sans Bengali", "Hind Siliguri", system-ui, -apple-system, sans-serif;
    background: radial-gradient(circle at 20% 0%, #1f3b5c 0%, #0e1621 55%, #0b1016 100%);
    color: #e9eef5; min-height: 100vh;
  }}
  .wrap {{ max-width: 720px; margin: 0 auto; }}
  header {{ text-align: center; margin-bottom: 28px; }}
  h1 {{ font-size: 24px; margin: 0 0 8px; font-weight: 700; letter-spacing: .2px; }}
  .sub {{ color: #8fa7c0; font-size: 14px; line-height: 1.7; }}
  .summary {{
    margin: 22px 0 26px; padding: 16px 18px; border-radius: 14px;
    background: rgba(255,255,255,.05); border: 1px solid rgba(255,255,255,.09);
    font-size: 13.5px; line-height: 1.9; color: #b9c9da;
  }}
  .summary b {{ color: #7fd4a8; }}
  .post {{ margin-bottom: 18px; }}
  .meta {{
    font-size: 12px; color: #7d93aa; margin-bottom: 6px; padding-left: 4px;
    font-family: ui-monospace, "SF Mono", Menlo, monospace;
  }}
  .bubble {{
    background: #182533; border-radius: 16px 16px 16px 4px;
    padding: 16px 18px; font-size: 15px; line-height: 1.85;
    border: 1px solid rgba(255,255,255,.06);
    box-shadow: 0 8px 24px rgba(0,0,0,.28); white-space: pre-wrap; word-break: break-word;
  }}
  .bubble :is(b, strong) {{ color: #ffffff; }}
  .actions {{
    font-size: 12px; color: #6f859c; margin-top: 6px; padding-left: 4px;
    letter-spacing: .3px;
  }}
  footer {{
    margin-top: 32px; text-align: center; color: #6b8299;
    font-size: 12.5px; line-height: 1.8;
  }}
  code {{ background: rgba(255,255,255,.07); padding: 2px 6px; border-radius: 6px; }}
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>টেলিগ্রাম চ্যানেল অটোপোস্ট — ডেমো রান</h1>
      <div class="sub">
        অন্য চ্যানেল থেকে ইনস্পিরেশন নিয়ে নতুন পোস্ট তৈরি ও রিভিউ কিউ —
        সম্পূর্ণ অফলাইনে, কোনো ক্রেডেনশিয়াল ছাড়াই।
        {live_note}
      </div>
    </header>
    <div class="summary">{''.join(summary_lines)}</div>
    {''.join(bubble_html) or '<div class="post">কোনো ড্রাফট তৈরি হয়নি।</div>'}
    <footer>
      বিস্তারিত: <code>README.md</code> · চালাতে: <code>autopost demo</code><br>
      আসল বট চালু করতে: <code>autopost login</code> →
      <code>autopost check</code> → <code>autopost run</code>
    </footer>
  </div>
</body>
</html>
"""


async def run_demo(output: Path, live: bool = False) -> Path:
    """Execute two pipeline passes against fixtures and write an HTML report."""
    data_dir = Path(tempfile.mkdtemp(prefix="autopost-demo-"))
    settings = _demo_settings(data_dir)
    summary_lines: list[str] = []
    packets: list[dict] = []

    async with Database(settings.db_path) as db:
        reader = DemoReader(settings)
        publisher = DemoPublisher()
        rewriter = Rewriter(settings) if live else DemoRewriter()
        pipeline = Pipeline(
            settings, db, reader=reader, rewriter=rewriter, publisher=publisher  # type: ignore[arg-type]
        )
        first = await pipeline.run_once(ignore_pause=True)
        summary_lines.append(
            f"<b>১ম রান</b> — স্ক্যান: {first.scanned} · নির্বাচিত: {first.candidates} · "
            f"ড্রাফট: {first.queued} · বাদ পড়েছে: {first.skipped}"
        )
        rows = await db.list_drafts(limit=20)
        for row in rows:
            packets.append(
                {
                    "source": row["source_channel"],
                    "text": row["text"],
                    "media_kind": row["media_kind"],
                }
            )

        # Second pass shows the dedupe ledger doing its job.
        reader.used = {(item["channel"], int(item["message_id"])) for item in FIXTURES}
        second = await pipeline.run_once(ignore_pause=True)
        summary_lines.append(
            f"<b>২য় রান (একই সোর্স)</b> — স্ক্যান: {second.scanned} · "
            f"নতুন ড্রাফট: {second.queued} <span style=\"color:#8fa7c0\">"
            "(ডুপ্লিকেট লেজার কাজ করছে)</span>"
        )

        stats = await db.stats()
        summary_lines.append(
            f"<b>ডাটাবেস</b> — দেখা পোস্ট: {stats['seen']} · "
            f"অপেক্ষমাণ ড্রাফট: {stats['pending']} · পাবলিশ: {stats['published']}"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_render_html(packets, summary_lines, live), encoding="utf-8")
    return output


def main(output: Path, live: bool = False) -> int:
    path = asyncio.run(run_demo(output, live=live))
    print(f"✅ ডেমো রিপোর্ট তৈরি: {path}")
    print(f"   {len(list(Path(path).parent.glob(path.name)))} ফাইল")
    return 0
