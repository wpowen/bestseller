from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class ChapterPublishMeta:
    chapter_number: int
    title: str | None
    word_count: int
    project_title: str
    project_slug: str
    idempotency_key: str | None = None


@dataclass
class PublishResult:
    success: bool
    platform_chapter_id: str | None = None
    platform_response: dict[str, Any] | None = None
    error_message: str | None = None
    retryable: bool = False
    error_kind: str | None = None


@dataclass
class PublishStatus:
    platform_chapter_id: str
    status: str  # "published" | "under_review" | "rejected" | "unknown"
    message: str | None = None


def normalize_publish_status(value: object) -> str:
    """Normalize platform-specific review values to the protocol vocabulary."""
    normalized = str(value).strip().lower()
    if normalized in {"0", "pending", "reviewing", "under_review", "under review"}:
        return "under_review"
    if normalized in {"1", "approved", "published", "online", "passed"}:
        return "published"
    if normalized in {"2", "rejected", "reject", "failed", "denied"}:
        return "rejected"
    return "unknown"


def is_business_auth_error(payload: object) -> bool:
    """Return whether a HTTP-success payload reports expired/invalid credentials."""
    if not isinstance(payload, dict):
        return False
    raw_code = payload.get("code", payload.get("statusCode", payload.get("errno")))
    if str(raw_code).strip().lower() in {
        "401",
        "403",
        "-401",
        "-403",
        "unauthorized",
        "forbidden",
    }:
        return True
    message = " ".join(
        str(payload.get(key, ""))
        for key in ("message", "msg", "error", "error_message")
    ).lower()
    return any(
        marker in message
        for marker in (
            "login",
            "auth",
            "cookie",
            "session expired",
            "token expired",
            "登录",
            "认证",
            "凭证",
            "会话过期",
        )
    )


@runtime_checkable
class PlatformAdapter(Protocol):
    """Protocol that all publishing platform adapters must implement."""

    platform_type: str
    supports_idempotency: bool

    async def authenticate(self) -> bool:
        """Verify credentials. Returns True if valid."""
        ...

    async def publish_chapter(
        self,
        content: str,
        meta: ChapterPublishMeta,
    ) -> PublishResult:
        """Publish a chapter. Returns PublishResult with platform-assigned ID."""
        ...

    async def check_publish_status(self, platform_chapter_id: str) -> PublishStatus:
        """Check whether a previously published chapter has been approved."""
        ...
