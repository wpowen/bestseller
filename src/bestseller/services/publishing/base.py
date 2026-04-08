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


@dataclass
class PublishResult:
    success: bool
    platform_chapter_id: str | None = None
    platform_response: dict[str, Any] | None = None
    error_message: str | None = None


@dataclass
class PublishStatus:
    platform_chapter_id: str
    status: str  # "published" | "under_review" | "rejected" | "unknown"
    message: str | None = None


@runtime_checkable
class PlatformAdapter(Protocol):
    """Protocol that all publishing platform adapters must implement."""

    platform_type: str

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
