"""Publishing to the target channel through the Telegram Bot API (aiogram)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import FSInputFile, LinkPreviewOptions

from .config import Settings

log = logging.getLogger("autopost.publisher")

TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024


class PublishError(RuntimeError):
    pass


def resolve_chat(value: str) -> str | int:
    """Accept `@name`, `-100…`, `t.me/name` or `https://t.me/name`."""
    ref = (value or "").strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        ref = ref.removeprefix(prefix)
    ref = ref.strip("/")
    if ref.lstrip("-").isdigit():
        return int(ref)
    if not ref:
        raise PublishError("TARGET_CHANNEL is empty")
    return ref if ref.startswith("@") else "@" + ref


def make_bot(settings: Settings) -> Bot:
    if not settings.bot_token:
        raise PublishError("BOT_TOKEN is not configured")
    return Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


class Publisher:
    """Sends posts (text or media+caption) with flood-wait handling."""

    def __init__(self, settings: Settings, bot: Bot | None = None) -> None:
        self.settings = settings
        self._bot = bot
        self._owns_bot = bot is None
        self.channel = resolve_chat(settings.target_channel) if settings.target_channel else None

    @property
    def bot(self) -> Bot:
        if self._bot is None:
            self._bot = make_bot(self.settings)
            self._owns_bot = True
        return self._bot

    async def aclose(self) -> None:
        if self._owns_bot and self._bot is not None:
            await self._bot.session.close()
            self._bot = None

    async def __aenter__(self) -> Publisher:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ send
    async def publish(
        self,
        text: str,
        media_kind: str | None = None,
        media_path: str | Path | None = None,
        *,
        chat_id: str | int | None = None,
        reply_markup: object = None,
        disable_notification: bool = False,
    ) -> int:
        """Send one post and return the resulting message id."""
        target = chat_id if chat_id is not None else self.channel
        if target is None:
            raise PublishError("no target channel configured")

        path = Path(media_path) if media_path else None
        usable_media = bool(path and path.exists() and path.stat().st_size > 0)
        kind = media_kind if usable_media else None

        if kind and len(text) > TELEGRAM_CAPTION_LIMIT:
            # Caption too long: send media alone and the full text right after.
            await self._send_media(target, path, "", disable_notification)
            return await self._send_text(target, text, reply_markup, disable_notification)

        if kind == "photo":
            return await self._send_media(
                target, path, text, disable_notification, reply_markup=reply_markup
            )
        if kind in {"video", "gif"}:
            return await self._send_media(
                target,
                path,
                text,
                disable_notification,
                reply_markup=reply_markup,
                as_video=kind == "video",
            )
        return await self._send_text(target, text, reply_markup, disable_notification)

    async def _send_text(
        self,
        chat_id: str | int,
        text: str,
        reply_markup: object = None,
        disable_notification: bool = False,
        plain_text: bool = False,
    ) -> int:
        body = text[:TELEGRAM_TEXT_LIMIT]
        try:
            return await self._call(
                lambda: self.bot.send_message(
                    chat_id=chat_id,
                    text=body,
                    reply_markup=reply_markup,  # type: ignore[arg-type]
                    disable_notification=disable_notification,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                    **({"parse_mode": None} if plain_text else {}),
                )
            )
        except PublishError as exc:
            if plain_text or "entit" not in str(exc).lower():
                raise
            log.warning("HTML parse failed, retrying as plain text")
            return await self._send_text(
                chat_id, body, reply_markup, disable_notification, plain_text=True
            )

    async def _send_media(
        self,
        chat_id: str | int,
        path: Path | None,
        caption: str,
        disable_notification: bool = False,
        reply_markup: object = None,
        as_video: bool = False,
        plain_text: bool = False,
    ) -> int:
        if path is None:
            return await self._send_text(chat_id, caption, reply_markup, disable_notification)
        file = FSInputFile(str(path))
        text = caption[:TELEGRAM_CAPTION_LIMIT] or None
        kwargs: dict[str, object] = {
            "chat_id": chat_id,
            "caption": text,
            "reply_markup": reply_markup,
            "disable_notification": disable_notification,
        }
        if plain_text:
            kwargs["parse_mode"] = None
        method = self.bot.send_video if as_video else self.bot.send_photo
        if as_video:
            kwargs["video"] = file
            kwargs["supports_streaming"] = True
        else:
            kwargs["photo"] = file
        return await self._call(lambda: method(**kwargs))  # type: ignore[arg-type]

    async def _call(self, factory) -> int:  # type: ignore[no-untyped-def]
        attempts = 0
        while True:
            attempts += 1
            try:
                message = await factory()
                return int(message.message_id)
            except TelegramRetryAfter as exc:
                wait = int(exc.retry_after) + 2
                log.warning("flood wait: sleeping %ss", wait)
                await asyncio.sleep(wait)
                if attempts >= 5:
                    raise PublishError("flood wait persisted") from exc
            except TelegramForbiddenError as exc:
                raise PublishError(
                    "the bot is not allowed to post there – add it as an admin of the channel"
                ) from exc
            except TelegramBadRequest as exc:
                raise PublishError(f"Telegram rejected the message: {exc.message}") from exc
            except TelegramNetworkError as exc:
                if attempts >= 3:
                    raise PublishError(f"network error while publishing: {exc}") from exc
                await asyncio.sleep(3 * attempts)

    # ---------------------------------------------------------------- helpers
    async def check_channel(self) -> str:
        """Verify the bot can see the target channel; returns its title."""
        if self.channel is None:
            raise PublishError("TARGET_CHANNEL is empty")
        chat = await self.bot.get_chat(self.channel)
        me = await self.bot.get_me()
        member = await self.bot.get_chat_member(self.channel, me.id)
        status = getattr(member, "status", "?")
        if str(status) not in {"administrator", "creator"}:
            raise PublishError(
                f"bot @{me.username} is '{status}' in {chat.title} – it must be an administrator"
            )
        return f"{chat.title} ({chat.id}), bot @{me.username}"
