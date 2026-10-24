"""Reading inspiration channels through a Telethon userbot session."""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import Message

from .config import Settings
from .db import utcnow
from .models import SourcePost

log = logging.getLogger("autopost.sources")

TME_RE = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me)/(?:s/)?", re.IGNORECASE)


def normalize_ref(value: str) -> str:
    """Turn `https://t.me/name`, `t.me/name`, `@name` or an id into a Telethon ref."""
    ref = (value or "").strip()
    ref = TME_RE.sub("", ref).rstrip("/")
    if ref.startswith(("joinchat", "+")):
        raise ValueError(f"invite links are not supported as sources: {value!r}")
    if ref and not ref.startswith("@") and not ref.lstrip("-").isdigit():
        ref = "@" + ref
    return ref


def display_name(ref: str) -> str:
    return ref.lstrip("@") if ref.startswith("@") else ref


def media_kind_of(message: Message) -> str | None:
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "video", None):
        return "gif" if getattr(message, "gif", False) else "video"
    document = getattr(message, "document", None)
    if document is not None:
        mime = getattr(document, "mime_type", "") or ""
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("image/"):
            return "photo"
    return None


class SourceReader:
    """Owns the Telethon client used for read-only channel access."""

    def __init__(self, settings: Settings, client: TelegramClient | None = None) -> None:
        self.settings = settings
        self._client = client

    # ----------------------------------------------------------------- client
    @property
    def client(self) -> TelegramClient:
        if self._client is None:
            raise RuntimeError("call connect() first")
        return self._client

    async def connect(self, allow_interactive: bool = False) -> TelegramClient:
        if self._client is not None:
            return self._client
        settings = self.settings
        if not settings.tg_api_id or not settings.tg_api_hash:
            raise RuntimeError("TG_API_ID and TG_API_HASH are required for the userbot")

        session: object
        if settings.tg_string_session:
            session = StringSession(settings.tg_string_session.get_secret_value())
        else:
            session_path = Path(settings.userbot_session)
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session = str(session_path)

        client = TelegramClient(
            session,  # type: ignore[arg-type]
            settings.tg_api_id,
            settings.tg_api_hash.get_secret_value(),
            device_model="autopost",
            system_version="1.0",
            app_version="autopost/1.0",
            auto_reconnect=True,
            connection_retries=5,
            request_retries=5,
        )
        await client.connect()
        if not await client.is_user_authorized():
            if not allow_interactive:
                await client.disconnect()
                raise RuntimeError(
                    "userbot session is not authorised – run `autopost login` on a machine "
                    "where you can type the login code"
                )
            await client.start()  # type: ignore[func-returns-value]
        me = await client.get_me()
        log.info("userbot connected as @%s (id=%s)", me.username or me.first_name, me.id)
        self._client = client
        return client

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def __aenter__(self) -> SourceReader:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()

    # ------------------------------------------------------------------ fetch
    async def fetch(
        self,
        channel: str,
        *,
        limit: int = 15,
        min_age_minutes: int | None = None,
        max_age_hours: int | None = None,
        download_media: bool | None = None,
    ) -> list[SourcePost]:
        """Return recent posts from one channel, newest first."""
        settings = self.settings
        ref = normalize_ref(channel)
        min_age = settings.min_source_age_minutes if min_age_minutes is None else min_age_minutes
        max_age = settings.max_source_age_hours if max_age_hours is None else max_age_hours
        want_media = settings.media_enabled if download_media is None else download_media

        now = utcnow()
        oldest_allowed = now - timedelta(hours=max_age)
        newest_allowed = now - timedelta(minutes=min_age)

        try:
            entity = await self.client.get_entity(ref)
            messages = await self.client.get_messages(entity, limit=limit)
        except Exception as exc:  # noqa: BLE001 - network / access errors are expected
            log.error("could not read %s: %s", channel, exc)
            return []

        posts: list[SourcePost] = []
        for message in messages or []:
            if not isinstance(message, Message):
                continue
            published = message.date.astimezone(now.tzinfo)
            if published > newest_allowed or published < oldest_allowed:
                continue
            kind = media_kind_of(message)
            text = (message.message or "").strip()
            if not text and kind is None:
                continue
            post = SourcePost(
                channel=display_name(ref),
                message_id=message.id,
                text=text,
                date=published,
                media_kind=kind,
                permalink=_permalink(ref, message),
                views=getattr(message, "views", None),
                forwards=getattr(message, "forwards", None),
                raw={"grouped_id": getattr(message, "grouped_id", None)},
            )
            if want_media and kind:
                post.media_path = await self._download(message, post)
            posts.append(post)
        log.info("fetched %s usable posts from %s", len(posts), channel)
        return posts

    # ---------------------------------------------------------------- media
    async def _download(self, message: Message, post: SourcePost) -> Path | None:
        settings = self.settings
        kind = post.media_kind
        if kind == "video":
            size = getattr(getattr(message, "file", None), "size", 0) or 0
            if size > settings.media_max_mb * 1024 * 1024:
                log.info("skipping media for %s: %.1f MB too large", post.key, size / 1048576)
                post.media_kind = None
                return None
        suffix = ".jpg" if kind == "photo" else ".mp4"
        target = settings.media_dir / f"{post.channel.replace('-', '')}_{post.message_id}{suffix}"
        settings.media_dir.mkdir(parents=True, exist_ok=True)
        try:
            downloaded = await self.client.download_media(message, file=str(target))
        except Exception as exc:  # noqa: BLE001
            log.warning("media download failed for %s: %s", post.key, exc)
            return None
        if not downloaded:
            return None
        path = Path(downloaded)
        if not path.exists() or path.stat().st_size == 0:
            return None
        return path


def _permalink(ref: str, message: Message) -> str | None:
    username = ref.lstrip("@") if ref.startswith("@") else ""
    if username and not username.lstrip("-").isdigit():
        return f"https://t.me/{username}/{message.id}"
    if ref.lstrip("-").isdigit():
        internal = str(ref).removeprefix("-100")
        return f"https://t.me/c/{internal}/{message.id}"
    return None
