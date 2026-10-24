"""The core loop: read sources → rewrite → publish or queue for review."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .ai import RewriteError, Rewriter, template_rewrite
from .config import Settings
from .db import Database, utcnow
from .models import Draft, Rewrite, SourcePost
from .publisher import Publisher
from .sources import SourceReader
from .textutil import (
    build_post_html,
    content_hash,
    escape,
    overlap,
    similarity,
    strip_promotional,
)

log = logging.getLogger("autopost.pipeline")

# A notifier receives (draft_id, text, media_kind, media_path) and shows it to a human.
Notifier = Callable[[int, str, "str | None", "str | None"], Awaitable[None]]


@dataclass
class RunSummary:
    scanned: int = 0
    candidates: int = 0
    published: int = 0
    queued: int = 0
    skipped: int = 0
    errors: int = 0
    details: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        self.details.append(message)
        log.info(message)

    def as_text(self) -> str:
        return (
            f"scanned={self.scanned} candidates={self.candidates} "
            f"published={self.published} queued={self.queued} "
            f"skipped={self.skipped} errors={self.errors}"
        )


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        reader: SourceReader | None = None,
        rewriter: Rewriter | None = None,
        publisher: Publisher | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.reader = reader
        self.rewriter = rewriter or Rewriter(settings)
        self.publisher = publisher
        self.notifier = notifier
        self.tz = ZoneInfo(settings.timezone)

    # ------------------------------------------------------------------ utils
    def local_now(self) -> datetime:
        return datetime.now(self.tz)

    def within_active_hours(self) -> bool:
        start, end = self.settings.active_hours_start, self.settings.active_hours_end
        if start is None or end is None:
            return True
        hour = self.local_now().hour
        if start == end:
            return True
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end  # window wraps midnight

    async def remaining_quota(self) -> int:
        today = self.local_now().date().isoformat()
        used = await self.db.count_published_on(today)
        return max(0, self.settings.daily_post_limit - used)

    # ------------------------------------------------------------------- run
    async def run_once(self, *, dry_run: bool = False, ignore_pause: bool = False) -> RunSummary:
        summary = RunSummary()
        if not ignore_pause and await self.db.is_paused():
            summary.note("pipeline is paused – doing nothing")
            return summary
        if not self.within_active_hours():
            summary.note("outside active hours – doing nothing")
            return summary
        if self.reader is None:
            raise RuntimeError("SourceReader is required for run_once()")

        quota = await self.remaining_quota()
        budget = min(self.settings.max_posts_per_run, quota)
        if budget <= 0:
            summary.note("daily post limit reached")
            return summary
        summary.note(
            f"run started: {len(self.settings.source_channels)} sources, "
            f"budget={budget}, mode={'auto' if self.settings.auto_publish else 'review'}"
        )

        posts: list[SourcePost] = []
        for channel in self.settings.source_channels:
            fetched = await self.reader.fetch(channel, limit=20)
            posts.extend(fetched)
        summary.scanned = len(posts)

        candidates = await self._select_candidates(posts, summary)
        summary.candidates = len(candidates)

        for post in candidates:
            if summary.published + summary.queued >= budget and not dry_run:
                summary.note("budget exhausted for this run")
                break
            try:
                outcome = await self.process(post, dry_run=dry_run)
            except Exception as exc:
                summary.errors += 1
                summary.note(f"error on {post.key}: {exc}")
                log.exception("unhandled error while processing %s", post.key)
                await self.db.mark_seen(post.channel, post.message_id, content_hash(post.text))
                continue

            if outcome == "published":
                summary.published += 1
            elif outcome == "queued":
                summary.queued += 1
            elif outcome == "error":
                summary.errors += 1
            else:
                summary.skipped += 1
            await asyncio.sleep(max(0, self.settings.publish_delay_seconds))

        await self.cleanup()
        summary.note("run finished: " + summary.as_text())
        return summary

    # -------------------------------------------------------------- selection
    async def _select_candidates(
        self, posts: list[SourcePost], summary: RunSummary
    ) -> list[SourcePost]:
        settings = self.settings
        chosen: list[SourcePost] = []
        batch_hashes: set[str] = set()
        recent = await self.db.recent_source_texts(
            settings.dedupe_window_hours, limit=60
        )
        recent_active = [text for text, status in recent if status != "rejected"]
        chosen_texts: list[str] = []

        for post in sorted(posts, key=lambda p: (p.engagement, p.date), reverse=True):
            if await self.db.is_seen(post.channel, post.message_id):
                continue
            reason = self.reject_reason(post)
            if reason:
                summary.skipped += 1
                log.debug("skip %s: %s", post.key, reason)
                await self.db.mark_seen(post.channel, post.message_id, content_hash(post.text))
                continue
            digest = content_hash(post.text)
            if digest in batch_hashes or await self.db.hash_seen_recently(
                digest, settings.dedupe_window_hours
            ):
                summary.skipped += 1
                log.info("skip %s: same story already handled", post.key)
                await self.db.mark_seen(post.channel, post.message_id, digest)
                continue
            twin = self._near_duplicate(post.text, recent_active + chosen_texts)
            if twin is not None:
                summary.skipped += 1
                log.info(
                    "skip %s: %.0f%% similar to an earlier draft (same story)",
                    post.key,
                    twin * 100,
                )
                await self.db.mark_seen(post.channel, post.message_id, digest)
                continue
            batch_hashes.add(digest)
            chosen_texts.append(post.text)
            chosen.append(post)
        return chosen

    def _near_duplicate(self, text: str, others: list[str]) -> float | None:
        """Return the highest similarity above the threshold, if any."""
        threshold = self.settings.duplicate_similarity_threshold
        if threshold <= 0 or not others:
            return None
        best = 0.0
        for other in others:
            score = overlap(text, other)
            if score >= threshold:
                best = max(best, score)
        return best or None

    def reject_reason(self, post: SourcePost) -> str | None:
        settings = self.settings
        text = post.text or ""
        if settings.require_media and not post.media_kind:
            return "media required but missing"
        cleaned = strip_promotional(text)
        if len(cleaned) < settings.min_source_chars:
            return f"source text too short ({len(cleaned)} chars)"
        lowered = cleaned.lower()
        for word in settings.blocked_keywords:
            if word.lower() in lowered:
                return f"blocked keyword: {word}"
        if post.permalink and post.permalink in cleaned:
            return "post only contains a link"
        return None

    # --------------------------------------------------------------- one post
    async def process(self, post: SourcePost, *, dry_run: bool = False) -> str:
        """Returns 'published' | 'queued' | 'skipped' | 'error'."""
        settings = self.settings
        try:
            rewrite = await self.rewriter.rewrite(post)
        except RewriteError as exc:
            if settings.allow_template_fallback:
                log.warning("AI failed for %s (%s) – using template fallback", post.key, exc)
                rewrite = template_rewrite(post, settings)
            else:
                log.error("AI rewrite failed for %s: %s", post.key, exc)
                await self.db.mark_seen(post.channel, post.message_id, content_hash(post.text))
                return "error"

        text = self.compose(post, rewrite)
        if not text.strip():
            await self.db.mark_seen(post.channel, post.message_id, content_hash(post.text))
            return "skipped"

        digest = content_hash(post.text)
        await self.db.mark_seen(post.channel, post.message_id, digest)

        if dry_run:
            log.info("[dry-run] would publish:\n%s", text)
            return "queued"

        draft_id = await self.db.create_draft(
            source_channel=post.channel,
            source_message_id=post.message_id,
            source_link=post.permalink,
            text=text,
            media_kind=post.media_kind,
            media_path=str(post.media_path) if post.media_path else None,
            content_hash=digest,
            status="pending" if not settings.auto_publish else "approved",
            notes=f"similarity={similarity(post.text, text):.2f}",
            source_text=post.text[:4000],
        )

        if settings.auto_publish:
            await self.publish_draft(draft_id)
            return "published"

        if self.notifier is None:
            log.warning("no review chat configured – draft %s stays in the database", draft_id)
            return "queued"
        try:
            await self.notifier(
                draft_id,
                text,
                post.media_kind,
                str(post.media_path) if post.media_path else None,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("could not deliver draft %s for review: %s", draft_id, exc)
        return "queued"

    def compose(self, post: SourcePost, rewrite: Rewrite) -> str:
        settings = self.settings
        body = rewrite.body
        if settings.include_source_credit and post.permalink:
            credit = settings.source_credit_template.format(
                link=post.permalink, channel=post.channel
            )
            body = f"{body}\n\n{credit}"
        has_media = bool(post.media_kind and settings.media_enabled)
        return build_post_html(
            headline=rewrite.headline,
            body=body,
            hashtags=rewrite.hashtags,
            signature=settings.signature,
            has_media=has_media,
            caption_limit=1000,
        )

    # --------------------------------------------------------------- publish
    async def publish_draft(self, draft_id: int) -> int:
        """Send an approved draft to the channel and record it."""
        row = await self.db.get_draft(draft_id)
        if row is None:
            raise ValueError(f"draft {draft_id} not found")
        if row["status"] == "published":
            raise ValueError(f"draft {draft_id} is already published")
        if not self.settings.auto_publish and row["status"] not in {"approved", "pending"}:
            raise ValueError(f"draft {draft_id} is {row['status']}, not approved")
        if self.publisher is None:
            raise RuntimeError("publisher is not configured")

        text = row["text"]
        media_kind = row["media_kind"] if self.settings.media_enabled else None
        media_path = row["media_path"] if media_kind else None

        message_id = await self.publisher.publish(text, media_kind, media_path)
        today = self.local_now().date().isoformat()
        await self.db.update_draft(
            draft_id,
            status="published",
            published_at=utcnow().isoformat(timespec="seconds"),
            target_message_id=message_id,
        )
        await self.db.record_publish(
            draft_id=draft_id,
            target_message_id=message_id,
            content_hash=row["content_hash"],
            publish_date=today,
        )
        if media_path:
            self._delete_media(Path(media_path))
        log.info("published draft %s as message %s", draft_id, message_id)
        return message_id

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _delete_media(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:  # pragma: no cover - filesystem race
            log.warning("could not delete %s: %s", path, exc)

    async def cleanup(self) -> None:
        expired = await self.db.expire_pending(older_than_hours=48)
        purged = await self.db.purge_seen(older_than_days=60)
        if expired or purged:
            log.info("cleanup: expired %s drafts, purged %s seen rows", expired, purged)

    # ------------------------------------------------------------- review API
    async def approve(self, draft_id: int) -> int:
        await self.db.update_draft(draft_id, status="approved")
        return await self.publish_draft(draft_id)

    async def reject(self, draft_id: int, reason: str = "rejected by editor") -> None:
        row = await self.db.get_draft(draft_id)
        if row is not None and row["media_path"]:
            self._delete_media(Path(row["media_path"]))
        await self.db.update_draft(draft_id, status="rejected", notes=reason)

    async def regenerate(self, draft_id: int) -> str:
        """Rewrite a queued draft again (used by the 🔄 button)."""
        row = await self.db.get_draft(draft_id)
        if row is None:
            raise ValueError(f"draft {draft_id} not found")
        post = SourcePost(
            channel=row["source_channel"],
            message_id=row["source_message_id"],
            text=row["text"],
            date=utcnow(),
            media_kind=row["media_kind"],
            media_path=Path(row["media_path"]) if row["media_path"] else None,
            permalink=row["source_link"],
        )
        # Feed the original source text when available so the model has real material.
        if row["source_text"]:
            post.text = row["source_text"]
        rewrite = await self.rewriter.rewrite(post)
        text = self.compose(post, rewrite)
        await self.db.update_draft(draft_id, text=text, status="pending")
        return text

    def draft_view(self, row) -> Draft:  # type: ignore[no-untyped-def]
        return Draft(
            id=row["id"],
            source_channel=row["source_channel"],
            source_message_id=row["source_message_id"],
            text=row["text"],
            media_kind=row["media_kind"],
            media_path=row["media_path"],
            source_link=row["source_link"],
            status=row["status"],
            created_at=row["created_at"],
            notes=row["notes"] or "",
        )


def draft_preview(text: str) -> str:
    """Short, HTML-safe excerpt for review cards."""
    return escape(text)[:3500]
