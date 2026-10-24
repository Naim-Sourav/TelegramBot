"""Long-running service: bot polling + periodic source scanning in one process."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from .ai import Rewriter
from .bot import ReviewBot
from .config import Settings
from .db import Database
from .pipeline import Pipeline
from .publisher import Publisher
from .sources import SourceReader

log = logging.getLogger("autopost.service")


async def scheduler_loop(
    pipeline: Pipeline, interval_minutes: int, run_lock: asyncio.Lock
) -> None:
    """Scan sources every `interval_minutes`, forever."""
    interval = max(1, interval_minutes) * 60
    while True:
        try:
            if run_lock.locked():
                log.info("previous run still active – skipping this tick")
            else:
                async with run_lock:
                    summary = await pipeline.run_once()
                    log.info("scheduled run: %s", summary.as_text())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduled run crashed")
        await asyncio.sleep(interval)


async def run_service(settings: Settings) -> None:
    settings.ensure_dirs()
    missing = settings.missing_for_write() + settings.missing_for_ai()
    if missing:
        raise SystemExit(
            "missing configuration: " + ", ".join(missing) + " – see .env.example"
        )

    async with Database(settings.db_path) as db:
        reader = SourceReader(settings)
        if settings.source_channels:
            try:
                await reader.connect()
            except Exception as exc:  # noqa: BLE001
                log.error("userbot unavailable, source reading disabled: %s", exc)
                reader = None  # type: ignore[assignment]
        else:
            log.warning("no SOURCE_CHANNELS configured – running in manual mode")
            reader = None

        rewriter = Rewriter(settings)
        publisher = Publisher(settings)
        pipeline = Pipeline(
            settings,
            db,
            reader=reader,
            rewriter=rewriter,
            publisher=publisher,
        )
        bot = ReviewBot(settings, db, pipeline)

        try:
            chan = await publisher.check_channel()
            log.info("target channel OK: %s", chan)
        except Exception as exc:  # noqa: BLE001
            log.error("target channel check failed: %s", exc)
            await bot.notify_owner(f"⚠️ টার্গেট চ্যানেল চেক ফেল: {exc}")

        scheduler = asyncio.create_task(
            scheduler_loop(pipeline, settings.poll_interval_minutes, bot.run_lock),
            name="scheduler",
        )
        log.info(
            "service started · %s sources · every %s min · mode=%s",
            len(settings.source_channels),
            settings.poll_interval_minutes,
            "auto" if settings.auto_publish else "review",
        )
        try:
            await bot.start()
        finally:
            scheduler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await scheduler
            await publisher.aclose()
            await rewriter.aclose()
            if reader is not None:
                await reader.disconnect()
            log.info("service stopped")
