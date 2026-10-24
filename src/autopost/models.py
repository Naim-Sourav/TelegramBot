"""Plain data containers shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class SourcePost:
    """A message read from an inspiration channel."""

    channel: str
    message_id: int
    text: str
    date: datetime
    media_kind: str | None = None  # "photo" | "video" | "gif" | None
    media_path: Path | None = None
    permalink: str | None = None
    views: int | None = None
    forwards: int | None = None
    raw: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.channel}:{self.message_id}"

    @property
    def full_text(self) -> str:
        return self.text or ""

    @property
    def engagement(self) -> int:
        return (self.views or 0) + 3 * (self.forwards or 0)


@dataclass(slots=True)
class Rewrite:
    """The AI's output for one source post."""

    headline: str
    body: str
    hashtags: list[str] = field(default_factory=list)
    alt_text: str = ""
    provider: str = ""
    model: str = ""

    @property
    def hashtags_line(self) -> str:
        if not self.hashtags:
            return ""
        return " ".join(tag for tag in self.hashtags if tag)


@dataclass(slots=True)
class Draft:
    """A ready-to-publish message, either queued for review or published."""

    id: int
    source_channel: str
    source_message_id: int
    text: str
    media_kind: str | None = None
    media_path: str | None = None
    source_link: str | None = None
    status: str = "pending"  # pending | approved | rejected | published | failed
    created_at: str = ""
    notes: str = ""
