"""Command line entry point: `autopost login | run | once | check | drafts | post`."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .config import Settings
from .logging_setup import setup_logging

log = logging.getLogger("autopost.cli")


def _settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    setup_logging(settings.log_level, settings.data_dir)
    return settings


# --------------------------------------------------------------------- login
async def _login(args: argparse.Namespace) -> int:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    settings = _settings()
    if not settings.tg_api_id or not settings.tg_api_hash:
        print("TG_API_ID এবং TG_API_HASH দরকার (https://my.telegram.org)।", file=sys.stderr)
        return 2

    if args.string:
        client: TelegramClient = TelegramClient(
            StringSession(), settings.tg_api_id, settings.tg_api_hash.get_secret_value()
        )
    else:
        session_path = Path(settings.userbot_session)
        session_path.parent.mkdir(parents=True, exist_ok=True)
        client = TelegramClient(
            str(session_path), settings.tg_api_id, settings.tg_api_hash.get_secret_value()
        )

    await client.start()  # type: ignore[func-returns-value]
    me = await client.get_me()
    print(f"✅ লগইন সফল: {me.first_name} (@{me.username}) id={me.id}")

    if args.string:
        print("\nTG_STRING_SESSION (সাবধানে রাখুন, এটা পাসওয়ার্ডের মতো):\n")
        print(StringSession.save(client.session))
    else:
        print(f"সেশন সেভ হয়েছে: {settings.userbot_session}.session")

    # Smoke test on the configured sources
    if settings.source_channels:
        print("\nসোর্স চ্যানেল চেক:")
        for channel in settings.source_channels:
            try:
                entity = await client.get_entity(channel)
                print(f"  ✅ {channel} → {getattr(entity, 'title', '?')}")
            except Exception as exc:  # noqa: BLE001
                print(f"  ❌ {channel} → {exc}")
    await client.disconnect()
    return 0


# -------------------------------------------------------------------- checks
async def _check(_: argparse.Namespace) -> int:
    from .ai import Rewriter, template_rewrite
    from .db import Database
    from .models import SourcePost
    from .publisher import Publisher
    from .sources import SourceReader

    settings = _settings()
    ok = True
    print(f"প্রোভাইডার : {settings.ai_provider} / {settings.ai_model}")
    print(f"চ্যানেল    : {settings.target_channel or '—'}")
    print(f"সোর্স      : {', '.join(settings.source_channels) or '—'}")
    print(f"মোড        : {'অটো-পাবলিশ' if settings.auto_publish else 'রিভিউ'}")

    missing = settings.missing_for_write() + settings.missing_for_ai()
    if missing:
        print("❌ মিসিং কনফিগ: " + ", ".join(missing))
        ok = False

    if not settings.missing_for_ai():
        print("\n[AI] একটি ছোট টেস্ট রিরাইট চলছে…")
        post = SourcePost(
            channel="test",
            message_id=1,
            text=(
                "Apple has announced that its new M4 chips will ship in the next MacBook Pro "
                "lineup, promising 20 percent faster performance and better battery life."
            ),
            date=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
        try:
            async with Rewriter(settings) as rewriter:
                rewrite = await rewriter.rewrite(post)
            print(f"  ✅ {rewrite.headline}")
            print(f"     {rewrite.body[:160]}…")
            print(f"     hashtags: {' '.join(rewrite.hashtags) or '—'}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  ❌ AI ব্যর্থ: {exc}")
            if settings.allow_template_fallback:
                fallback = template_rewrite(post, settings)
                print(f"     fallback টেমপ্লেট কাজ করবে: {fallback.headline[:60]}")

    if not settings.missing_for_write():
        print("\n[Bot] চ্যানেল অ্যাক্সেস চেক…")
        publisher = Publisher(settings)
        try:
            info = await publisher.check_channel()
            print(f"  ✅ {info}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  ❌ {exc}")
        finally:
            await publisher.aclose()

    if settings.source_channels:
        print("\n[Userbot] সোর্স চ্যানেল পড়া যাচ্ছে কি না…")
        reader = SourceReader(settings)
        try:
            await reader.connect()
            posts = await reader.fetch(settings.source_channels[0], limit=5, download_media=False)
            print(f"  ✅ {len(posts)} পোস্ট পড়া গেছে")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  ❌ {exc}")
        finally:
            await reader.disconnect()

    async with Database(settings.db_path) as db:
        stats = await db.stats()
    print(f"\n[DB] seen={stats['seen']} pending={stats['pending']} published={stats['published']}")
    print("\n" + ("✅ সব ঠিক আছে" if ok else "⚠️ উপরের সমস্যাগুলো ঠিক করুন"))
    return 0 if ok else 1


# ----------------------------------------------------------------------- run
async def _once(args: argparse.Namespace) -> int:
    from .ai import Rewriter
    from .db import Database
    from .pipeline import Pipeline
    from .publisher import Publisher
    from .sources import SourceReader

    settings = _settings()
    missing = settings.missing_for_ai()
    if missing:
        print("❌ " + ", ".join(missing), file=sys.stderr)
        return 2

    async with Database(settings.db_path) as db:
        reader = SourceReader(settings)
        await reader.connect()
        rewriter = Rewriter(settings)
        publisher = Publisher(settings)
        pipeline = Pipeline(
            settings, db, reader=reader, rewriter=rewriter, publisher=publisher
        )
        try:
            summary = await pipeline.run_once(dry_run=args.dry_run, ignore_pause=args.force)
        finally:
            await reader.disconnect()
            await publisher.aclose()
            await rewriter.aclose()

    print(json.dumps(
        {
            "scanned": summary.scanned,
            "candidates": summary.candidates,
            "published": summary.published,
            "queued": summary.queued,
            "skipped": summary.skipped,
            "errors": summary.errors,
        },
        ensure_ascii=False,
    ))
    return 0


async def _run(_: argparse.Namespace) -> int:
    from .service import run_service

    settings = _settings()
    await run_service(settings)
    return 0


# ---------------------------------------------------------------------- demo
async def _demo(args: argparse.Namespace) -> int:
    from .demo import run_demo

    _settings()  # logging setup
    path = await run_demo(Path(args.output), live=args.live)
    print(f"✅ ডেমো রিপোর্ট তৈরি হয়েছে: {path}")
    print("   ব্রাউজারে খুলে দেখুন — এটাই বটের আউটপুট ফরম্যাট।")
    return 0


# -------------------------------------------------------------------- drafts
async def _drafts(args: argparse.Namespace) -> int:
    from .db import Database

    settings = _settings()
    async with Database(settings.db_path) as db:
        rows = await db.list_drafts(status=args.status, limit=args.limit)
        if not rows:
            print("কোনো ড্রাফট নেই।")
            return 0
        for row in rows:
            first = (row["text"] or "").split("\n")[0][:80]
            print(f"#{row['id']:>3} [{row['status']:<9}] {row['source_channel']:<20} {first}")
    return 0


# --------------------------------------------------------------- manual post
async def _post(args: argparse.Namespace) -> int:
    from .publisher import Publisher

    settings = _settings()
    text = args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    if not text.strip():
        print("টেক্সট দরকার: --text বা --file", file=sys.stderr)
        return 2
    publisher = Publisher(settings)
    try:
        message_id = await publisher.publish(text.strip(), args.media_kind, args.media)
    finally:
        await publisher.aclose()
    print(f"✅ পাঠানো হয়েছে (message_id={message_id})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autopost", description="Telegram channel autopilot"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="userbot লগইন (OTP সহ)")
    p_login.add_argument("--string", action="store_true", help="string session প্রিন্ট করবে")
    p_login.set_defaults(func=_login)

    p_check = sub.add_parser("check", help="কনফিগ, AI, বট ও সোর্স যাচাই")
    p_check.set_defaults(func=_check)

    p_once = sub.add_parser("once", help="একবার স্ক্যান করে পোস্ট/কিউ করবে (cron-এর জন্য)")
    p_once.add_argument("--dry-run", action="store_true", help="কিছু পাবলিশ/সেভ করবে না")
    p_once.add_argument("--force", action="store_true", help="পজ ও অ্যাক্টিভ আওয়ার উপেক্ষা")
    p_once.set_defaults(func=_once)

    p_run = sub.add_parser("run", help="পুরো সার্ভিস চালু (বট + শিডিউলার)")
    p_run.set_defaults(func=_run)

    p_demo = sub.add_parser("demo", help="ক্রেডেনশিয়াল ছাড়াই অফলাইন ডেমো রান")
    p_demo.add_argument("--output", default="data/demo_preview.html")
    p_demo.add_argument("--live", action="store_true", help="আসল AI ব্যবহার করবে")
    p_demo.set_defaults(func=_demo)

    p_drafts = sub.add_parser("drafts", help="ড্রাফটের তালিকা")
    p_drafts.add_argument("--status", default="pending")
    p_drafts.add_argument("--limit", type=int, default=20)
    p_drafts.set_defaults(func=_drafts)

    p_post = sub.add_parser("post", help="হাতে একটা পোস্ট পাঠান (টেস্ট)")
    p_post.add_argument("--text", default="")
    p_post.add_argument("--file", default="")
    p_post.add_argument("--media", default=None)
    p_post.add_argument("--media-kind", default="photo", choices=["photo", "video", "gif"])
    p_post.set_defaults(func=_post)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command not in {"login", "check", "once", "run", "drafts", "post", "demo"}:
        parser.print_help()
        return 2
    try:
        return asyncio.run(args.func(args))
    except KeyboardInterrupt:
        print("\nবন্ধ করা হলো।")
        return 130
    except SystemExit as exc:
        if exc.code:
            print(str(exc), file=sys.stderr)
        return int(exc.code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
