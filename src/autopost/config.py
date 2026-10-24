"""Configuration loaded from environment variables / .env file."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_csv(value: object) -> object:
    """Allow `A,B,C` or newline separated values for list fields."""
    if isinstance(value, str):
        items = [part.strip() for part in value.replace("\n", ",").split(",")]
        return [item for item in items if item]
    return value


CsvList = Annotated[list[str], NoDecode]
CsvIntList = Annotated[list[int], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- telegram
    # https://my.telegram.org -> API development tools (needed for the userbot)
    tg_api_id: int | None = None
    tg_api_hash: SecretStr | None = None
    # Name of the local .session file created by `autopost login`
    userbot_session: str = "data/userbot"
    # Optional: log in with an existing string session instead of a file
    tg_string_session: SecretStr | None = None

    # Bot that publishes to the channel and shows the review buttons
    bot_token: SecretStr | None = None
    target_channel: str = ""  # @mychannel or -1001234567890
    # Where drafts are sent for approval. Defaults to the owner's DM.
    review_chat_id: int | None = None
    owner_user_ids: CsvIntList = Field(default_factory=list)

    # ---------------------------------------------------------------- sources
    source_channels: CsvList = Field(default_factory=list)
    poll_interval_minutes: int = 30
    max_posts_per_run: int = 3
    daily_post_limit: int = 12
    # Only consider source posts between these ages (minutes / hours)
    min_source_age_minutes: int = 10
    max_source_age_hours: int = 72
    min_source_chars: int = 120
    require_media: bool = False
    # Skip source posts containing any of these words (case-insensitive)
    blocked_keywords: CsvList = Field(default_factory=list)
    # Skip sources whose latest post repeats something already used
    # 0.75 works well for "same story reposted"; raise it to allow closer repeats
    duplicate_similarity_threshold: float = 0.75
    dedupe_window_hours: int = 96

    # --------------------------------------------------------------------- ai
    ai_provider: Literal["openai", "gemini"] = "openai"
    ai_api_key: SecretStr | None = None
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model: str = "gpt-4o-mini"
    ai_temperature: float = 0.8
    ai_timeout_seconds: float = 90.0
    ai_max_retries: int = 3
    # Reject a rewrite that is still too close to the source text (0..1)
    max_source_similarity: float = 0.45
    # If the AI keeps failing, publish a cleaned-up template instead of skipping
    allow_template_fallback: bool = False

    # ------------------------------------------------------------------ style
    post_language: str = "bn"
    channel_title: str = "আমাদের চ্যানেল"
    channel_persona: str = (
        "বাংলাভাষী পাঠকদের জন্য তথ্যসমৃদ্ধ, সহজ ও বিশ্বাসযোগ্য কনটেন্ট। "
        "ক্লিকবেইট নয়, ক্লিন ও গোছানো লেখা।"
    )
    # Extra hard rules for the model, one per line
    style_rules: CsvList = Field(default_factory=list)
    max_post_chars: int = 900
    hashtag_count: int = 3
    signature: str = ""  # e.g. "📌 @mychannel"
    include_source_credit: bool = False
    source_credit_template: str = "সূত্র: {link}"

    # ------------------------------------------------------------- publishing
    auto_publish: bool = False  # False = every draft needs an ✅ approval
    media_enabled: bool = True
    media_max_mb: int = 45
    # Only publish between these hours (local time of `timezone`); empty = always
    active_hours_start: int | None = None
    active_hours_end: int | None = None
    timezone: str = "Asia/Dhaka"
    publish_delay_seconds: int = 5

    # ------------------------------------------------------------------ infra
    data_dir: Path = Path("data")
    log_level: str = "INFO"

    # ------------------------------------------------------------- validators
    @field_validator(
        "source_channels",
        "blocked_keywords",
        "style_rules",
        mode="before",
    )
    @classmethod
    def _csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("owner_user_ids", mode="before")
    @classmethod
    def _ints(cls, value: object) -> list[int]:
        items = _split_csv(value)
        if not isinstance(items, list):
            items = [items]
        return [int(str(item).strip()) for item in items if str(item).strip()]

    @field_validator("target_channel")
    @classmethod
    def _strip_channel(cls, value: str) -> str:
        return value.strip()

    # ---------------------------------------------------------------- helpers
    @property
    def db_path(self) -> Path:
        return self.data_dir / "autopost.sqlite3"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def session_path(self) -> Path:
        return self.data_dir / "userbot"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)

    def missing_for_read(self) -> list[str]:
        missing = []
        if not self.tg_api_id or not self.tg_api_hash:
            missing.append("TG_API_ID / TG_API_HASH")
        if not self.source_channels:
            missing.append("SOURCE_CHANNELS")
        return missing

    def missing_for_write(self) -> list[str]:
        missing = []
        if not self.bot_token:
            missing.append("BOT_TOKEN")
        if not self.target_channel:
            missing.append("TARGET_CHANNEL")
        return missing

    def missing_for_ai(self) -> list[str]:
        return [] if self.ai_api_key else ["AI_API_KEY"]

    def review_target(self) -> int | None:
        """Chat that receives drafts for approval."""
        if self.review_chat_id:
            return self.review_chat_id
        if len(self.owner_user_ids) == 1:
            return self.owner_user_ids[0]
        return None
