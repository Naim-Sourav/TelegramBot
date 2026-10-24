"""Telegram bot: review cards, approval buttons and owner commands."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

from .config import Settings
from .db import Database
from .pipeline import Pipeline
from .textutil import escape

log = logging.getLogger("autopost.bot")

HELP = (
    "<b>অটোপোস্ট বট</b>\n\n"
    "<b>কমান্ড</b>\n"
    "/status – অবস্থা, আজকের কোটা, পজ নাকি চালু\n"
    "/run – এখনই একবার সোর্স চ্যানেল স্ক্যান করবে\n"
    "/queue – অপেক্ষমাণ ড্রাফটের তালিকা\n"
    "/draft &lt;id&gt; – একটা ড্রাফট দেখাবে\n"
    "/publish &lt;id&gt; – ড্রাফট পাবলিশ করবে\n"
    "/reject &lt;id&gt; – ড্রাফট বাতিল করবে\n"
    "/regen &lt;id&gt; – নতুন করে রিরাইট করাবে\n"
    "/edit &lt;id&gt; – এরপর পাঠানো টেক্সট দিয়ে ড্রাফট বদলাবে\n"
    "/pause – অটোমেশন থামাবে\n"
    "/resume – আবার চালু করবে\n"
    "/channels – সোর্স চ্যানেলের তালিকা\n\n"
    "প্রতিটি ড্রাফটের নিচে ✅ / 🔄 / ✏️ / ❌ বাটন থাকবে।"
)


def review_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ পাবলিশ", callback_data=f"publish:{draft_id}"),
                InlineKeyboardButton(text="🔄 রিরাইট", callback_data=f"regen:{draft_id}"),
            ],
            [
                InlineKeyboardButton(text="✏️ এডিট", callback_data=f"edit:{draft_id}"),
                InlineKeyboardButton(text="❌ বাতিল", callback_data=f"reject:{draft_id}"),
            ],
        ]
    )


class ReviewBot:
    """Owns the aiogram dispatcher and exposes the pipeline notifier hook."""

    def __init__(self, settings: Settings, db: Database, pipeline: Pipeline) -> None:
        self.settings = settings
        self.db = db
        self.pipeline = pipeline
        self.bot = Bot(
            token=settings.bot_token.get_secret_value() if settings.bot_token else "",
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.dp = Dispatcher()
        self.router = Router()
        self.dp.include_router(self.router)
        self.run_lock = asyncio.Lock()
        self._awaiting_edit: dict[int, int] = {}  # user_id -> draft_id
        self._register()
        pipeline.notifier = self.notify_draft

    # ------------------------------------------------------------- permissions
    def is_owner(self, user_id: int) -> bool:
        owners = self.settings.owner_user_ids
        return True if not owners else user_id in owners

    async def guard(self, message: Message) -> bool:
        if self.is_owner(message.from_user.id):  # type: ignore[union-attr]
            return True
        await message.answer("⛔ এই বট শুধু চ্যানেলের মালিকের জন্য।")
        log.warning("blocked non-owner %s", message.from_user.id)  # type: ignore[union-attr]
        return False

    # --------------------------------------------------------------- handlers
    def _register(self) -> None:
        router = self.router

        @router.message(CommandStart())
        async def start(message: Message) -> None:
            if not await self.guard(message):
                return
            await message.answer(HELP)
            if not self.settings.owner_user_ids:
                await message.answer(
                    "⚠️ OWNER_USER_IDS খালি, তাই যে কেউ এই বট চালাতে পারবে। "
                    f"আপনার আইডি: <code>{message.from_user.id}</code> – এটা .env এ যোগ করুন।"  # type: ignore[union-attr]
                )

        @router.message(Command("help"))
        async def help_cmd(message: Message) -> None:
            await message.answer(HELP)

        @router.message(Command("status"))
        async def status(message: Message) -> None:
            if not await self.guard(message):
                return
            stats = await self.db.stats()
            quota = self.pipeline.settings.daily_post_limit
            used = max(0, quota - await self.pipeline.remaining_quota())
            paused = await self.db.is_paused()
            mode = "অটো-পাবলিশ" if self.settings.auto_publish else "রিভিউ মোড"
            lines = [
                "<b>অবস্থা</b>",
                f"মোড: {mode}",
                f"অটোমেশন: {'⏸ পজ' if paused else '▶️ চালু'}",
                f"আজ পাবলিশ: {used}/{quota}",
                f"অপেক্ষমাণ ড্রাফট: {stats['pending']}",
                f"মোট পাবলিশ: {stats['published']}",
                f"দেখা পোস্ট: {stats['seen']}",
                f"সোর্স: {len(self.settings.source_channels)}টি",
                f"চ্যানেল: {escape(self.settings.target_channel or '—')}",
            ]
            await message.answer("\n".join(lines))

        @router.message(Command("pause"))
        async def pause(message: Message) -> None:
            if not await self.guard(message):
                return
            await self.db.set_paused(True)
            await message.answer("⏸ অটোমেশন পজ করা হলো। /resume দিয়ে চালু করুন।")

        @router.message(Command("resume"))
        async def resume(message: Message) -> None:
            if not await self.guard(message):
                return
            await self.db.set_paused(False)
            await message.answer("▶️ অটোমেশন আবার চালু হলো।")

        @router.message(Command("run"))
        async def run_now(message: Message) -> None:
            if not await self.guard(message):
                return
            if self.run_lock.locked():
                await message.answer("⏳ আগের রান এখনো চলছে, একটু অপেক্ষা করুন।")
                return
            async with self.run_lock:
                note = await message.answer("🔎 সোর্স চ্যানেল স্ক্যান করা হচ্ছে…")
                try:
                    summary = await self.pipeline.run_once(ignore_pause=True)
                except Exception as exc:
                    log.exception("manual run failed")
                    await note.edit_text(f"❌ রান ফেল করেছে: {escape(str(exc))}")
                    return
                await note.edit_text(
                    "✅ রান শেষ\n\n"
                    f"স্ক্যান: {summary.scanned}\n"
                    f"নির্বাচিত: {summary.candidates}\n"
                    f"পাবলিশ: {summary.published}\n"
                    f"রিভিউতে: {summary.queued}\n"
                    f"স্কিপ: {summary.skipped}\n"
                    f"এরর: {summary.errors}"
                )

        @router.message(Command("queue"))
        async def queue(message: Message) -> None:
            if not await self.guard(message):
                return
            rows = await self.db.list_drafts(status="pending", limit=15)
            if not rows:
                await message.answer("কোনো অপেক্ষমাণ ড্রাফট নেই।")
                return
            lines = ["<b>অপেক্ষমাণ ড্রাফট</b>"]
            for row in rows:
                first = (row["text"] or "").split("\n")[0][:70]
                lines.append(f"#{row['id']} · {escape(row['source_channel'])} · {escape(first)}")
            await message.answer("\n".join(lines))

        @router.message(Command("draft"))
        async def show_draft(message: Message, command: CommandObject) -> None:
            if not await self.guard(message):
                return
            draft_id = self._parse_id(command)
            if draft_id is None:
                await message.answer("ব্যবহার: /draft &lt;id&gt;")
                return
            await self.send_draft_card(message.chat.id, draft_id)

        @router.message(Command("publish"))
        async def publish(message: Message, command: CommandObject) -> None:
            if not await self.guard(message):
                return
            draft_id = self._parse_id(command)
            if draft_id is None:
                await message.answer("ব্যবহার: /publish &lt;id&gt;")
                return
            try:
                msg_id = await self.pipeline.approve(draft_id)
            except Exception as exc:  # noqa: BLE001
                await message.answer(f"❌ পাবলিশ হয়নি: {escape(str(exc))}")
                return
            await message.answer(f"✅ ড্রাফট #{draft_id} পাবলিশ হয়েছে (message {msg_id})।")

        @router.message(Command("reject"))
        async def reject(message: Message, command: CommandObject) -> None:
            if not await self.guard(message):
                return
            draft_id = self._parse_id(command)
            if draft_id is None:
                await message.answer("ব্যবহার: /reject &lt;id&gt;")
                return
            await self.pipeline.reject(draft_id)
            await message.answer(f"❌ ড্রাফট #{draft_id} বাতিল করা হলো।")

        @router.message(Command("regen"))
        async def regen(message: Message, command: CommandObject) -> None:
            if not await self.guard(message):
                return
            draft_id = self._parse_id(command)
            if draft_id is None:
                await message.answer("ব্যবহার: /regen &lt;id&gt;")
                return
            note = await message.answer("🔄 নতুন করে লেখা হচ্ছে…")
            try:
                await self.pipeline.regenerate(draft_id)
            except Exception as exc:  # noqa: BLE001
                await note.edit_text(f"❌ রিরাইট ব্যর্থ: {escape(str(exc))}")
                return
            await note.delete()
            await self.send_draft_card(message.chat.id, draft_id)

        @router.message(Command("edit"))
        async def edit(message: Message, command: CommandObject) -> None:
            if not await self.guard(message):
                return
            draft_id = self._parse_id(command)
            if draft_id is None:
                await message.answer("ব্যবহার: /edit &lt;id&gt;, তারপর নতুন টেক্সট পাঠান।")
                return
            row = await self.db.get_draft(draft_id)
            if row is None:
                await message.answer("ড্রাফট পাওয়া যায়নি।")
                return
            self._awaiting_edit[message.from_user.id] = draft_id  # type: ignore[union-attr]
            await message.answer(
                f"✏️ ড্রাফট #{draft_id} এর নতুন টেক্সট পাঠান (HTML সাপোর্ট করে না, সাধারণ টেক্সট)।"
            )

        @router.message(Command("channels"))
        async def channels(message: Message) -> None:
            if not await self.guard(message):
                return
            if not self.settings.source_channels:
                await message.answer("কোনো সোর্স চ্যানেল সেট করা নেই (SOURCE_CHANNELS)।")
                return
            listing = "\n".join(f"• {escape(ch)}" for ch in self.settings.source_channels)
            await message.answer(
                f"<b>সোর্স চ্যানেল ({len(self.settings.source_channels)})</b>\n{listing}\n\n"
                f"টার্গেট: {escape(self.settings.target_channel or '—')}\n"
                f"ইন্টারভাল: {self.settings.poll_interval_minutes} মিনিট"
            )

        @router.message(F.text, ~F.text.startswith("/"))
        async def free_text(message: Message) -> None:
            user_id = message.from_user.id  # type: ignore[union-attr]
            draft_id = self._awaiting_edit.pop(user_id, None)
            if draft_id is None:
                return
            if not await self.guard(message):
                return
            text = (message.html_text or "").strip()
            if not text:
                await message.answer("খালি টেক্সট গ্রহণ করা হলো না।")
                return
            await self.db.update_draft(draft_id, text=text, status="pending")
            await message.answer(f"✅ ড্রাফট #{draft_id} আপডেট হয়েছে।")
            await self.send_draft_card(message.chat.id, draft_id)

        @router.callback_query(F.data.startswith("publish:"))
        async def cb_publish(call: CallbackQuery) -> None:
            draft_id = self._cb_id(call)
            if draft_id is None:
                return
            if not self.is_owner(call.from_user.id):
                await call.answer("অনুমতি নেই", show_alert=True)
                return
            await call.answer("পাবলিশ হচ্ছে…")
            try:
                msg_id = await self.pipeline.approve(draft_id)
            except Exception as exc:  # noqa: BLE001
                await call.message.answer(f"❌ পাবলিশ হয়নি: {escape(str(exc))}")  # type: ignore[union-attr]
                return
            await self._finish_card(call, f"✅ পাবলিশ হয়েছে (message {msg_id})")

        @router.callback_query(F.data.startswith("reject:"))
        async def cb_reject(call: CallbackQuery) -> None:
            draft_id = self._cb_id(call)
            if draft_id is None:
                return
            if not self.is_owner(call.from_user.id):
                await call.answer("অনুমতি নেই", show_alert=True)
                return
            await self.pipeline.reject(draft_id)
            await call.answer("বাতিল করা হলো")
            await self._finish_card(call, "❌ বাতিল")

        @router.callback_query(F.data.startswith("regen:"))
        async def cb_regen(call: CallbackQuery) -> None:
            draft_id = self._cb_id(call)
            if draft_id is None:
                return
            if not self.is_owner(call.from_user.id):
                await call.answer("অনুমতি নেই", show_alert=True)
                return
            await call.answer("নতুন করে লেখা হচ্ছে…")
            try:
                await self.pipeline.regenerate(draft_id)
            except Exception as exc:  # noqa: BLE001
                await call.message.answer(f"❌ রিরাইট ব্যর্থ: {escape(str(exc))}")  # type: ignore[union-attr]
                return
            await self._finish_card(call, "🔄 নতুন ভার্সন নিচে")
            await self.send_draft_card(call.message.chat.id, draft_id)  # type: ignore[union-attr]

        @router.callback_query(F.data.startswith("edit:"))
        async def cb_edit(call: CallbackQuery) -> None:
            draft_id = self._cb_id(call)
            if draft_id is None:
                return
            if not self.is_owner(call.from_user.id):
                await call.answer("অনুমতি নেই", show_alert=True)
                return
            self._awaiting_edit[call.from_user.id] = draft_id
            await call.answer()
            await call.message.answer(  # type: ignore[union-attr]
                f"✏️ ড্রাফট #{draft_id} এর নতুন টেক্সট পাঠান।"
            )

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _parse_id(command: CommandObject) -> int | None:
        if not command.args:
            return None
        token = command.args.split()[0].lstrip("#")
        return int(token) if token.isdigit() else None

    @staticmethod
    def _cb_id(call: CallbackQuery) -> int | None:
        try:
            return int((call.data or "").split(":", 1)[1])
        except (IndexError, ValueError):
            return None

    async def _finish_card(self, call: CallbackQuery, label: str) -> None:
        message = call.message
        if message is None:
            return
        try:
            await message.edit_reply_markup(reply_markup=None)
            await message.reply(label)
        except Exception:
            log.debug("could not update review card", exc_info=True)

    async def send_draft_card(self, chat_id: int, draft_id: int) -> Message | None:
        row = await self.db.get_draft(draft_id)
        if row is None:
            return None
        if row["media_path"] and Path(row["media_path"]).exists():
            try:
                await self.bot.send_photo(chat_id=chat_id, photo=FSInputFile(row["media_path"]))
            except Exception:
                log.debug("could not preview media for draft %s", draft_id, exc_info=True)
        header = (
            f"🔖 <b>ড্রাফট #{draft_id}</b> · {escape(row['source_channel'])}"
            f" · {escape(row['notes'] or '')}"
        )
        body = row["text"]
        return await self.bot.send_message(
            chat_id=chat_id,
            text=f"{header}\n\n{body}",
            reply_markup=review_keyboard(draft_id),
            link_preview_options=LinkPreviewOptions(is_disabled=bool(row["source_link"])),
        )

    async def notify_draft(
        self, draft_id: int, text: str, media_kind: str | None, media_path: str | None
    ) -> None:
        """Pipeline hook: send a new draft to the reviewer."""
        chat_id = self.settings.review_target()
        if chat_id is None:
            log.warning("no review chat (set OWNER_USER_IDS or REVIEW_CHAT_ID)")
            return
        if media_path and Path(media_path).exists():
            try:
                file = FSInputFile(media_path)
                if media_kind == "photo":
                    await self.bot.send_photo(chat_id=chat_id, photo=file)
                elif media_kind in {"video", "gif"}:
                    await self.bot.send_video(chat_id=chat_id, video=file)
            except Exception:
                log.warning("media preview failed for draft %s", draft_id, exc_info=True)

        prefix = f"🆕 <b>নতুন ড্রাফট #{draft_id}</b>"
        await self.bot.send_message(
            chat_id=chat_id,
            text=f"{prefix}\n\n{text}",
            reply_markup=review_keyboard(draft_id),
        )

    async def notify_owner(self, text: str) -> None:
        chat_id = self.settings.review_target()
        if chat_id is None:
            return
        try:
            await self.bot.send_message(chat_id=chat_id, text=text)
        except Exception:
            log.warning("could not notify owner", exc_info=True)

    # -------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        await self.bot.delete_webhook(drop_pending_updates=True)
        await self.dp.start_polling(self.bot)


async def run_bot_only(settings: Settings, db: Database, pipeline: Pipeline) -> None:
    bot = ReviewBot(settings, db, pipeline)
    try:
        await bot.start()
    finally:
        await bot.bot.session.close()
