from __future__ import annotations

"""番茄小说 publishing adapter.

Authentication: cookie-based session (from browser login) or API token.
Credentials expected keys: {"cookie": "...", "book_id": "..."}

NOTE: 番茄小说 does not publish a public writer API. This adapter uses the
internal web API observed from the author backend (https://fanqienovel.com/writer).
Treat as best-effort; update endpoint paths if they change.
"""

import logging
from typing import Any

import httpx

from bestseller.services.publishing.base import (
    ChapterPublishMeta,
    PlatformAdapter,
    PublishResult,
    PublishStatus,
)

logger = logging.getLogger(__name__)

_DEFAULT_BASE = "https://fanqienovel.com"


class FanqieAdapter:
    platform_type = "fanqie"

    def __init__(self, credentials: dict[str, Any], api_base_url: str | None = None) -> None:
        self._cookie = credentials.get("cookie", "")
        self._book_id = credentials.get("book_id", "")
        self._base = (api_base_url or _DEFAULT_BASE).rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Cookie": self._cookie,
            "User-Agent": "Mozilla/5.0 (compatible; BestSeller/1.0)",
            "Referer": f"{self._base}/writer/",
        }

    async def authenticate(self) -> bool:
        if not self._cookie or not self._book_id:
            logger.warning("FanqieAdapter: missing cookie or book_id")
            return False
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self._base}/writer/api/book/detail",
                    params={"book_id": self._book_id},
                    headers=self._headers(),
                )
            return resp.status_code == 200
        except httpx.HTTPError as exc:
            logger.error("FanqieAdapter.authenticate error: %s", exc)
            return False

    async def publish_chapter(
        self,
        content: str,
        meta: ChapterPublishMeta,
    ) -> PublishResult:
        payload = {
            "book_id": self._book_id,
            "title": meta.title or f"第{meta.chapter_number}章",
            "content": content,
            "chapter_word_number": meta.word_count,
        }
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self._base}/writer/api/chapter/publish",
                    json=payload,
                    headers=self._headers(),
                )
            data = resp.json()
            if resp.status_code == 200 and data.get("code") == 0:
                chapter_id = str(data.get("data", {}).get("item_id", ""))
                return PublishResult(
                    success=True,
                    platform_chapter_id=chapter_id,
                    platform_response=data,
                )
            return PublishResult(
                success=False,
                platform_response=data,
                error_message=data.get("message", "Publish failed"),
            )
        except httpx.HTTPError as exc:
            logger.error("FanqieAdapter.publish_chapter error: %s", exc)
            return PublishResult(success=False, error_message=str(exc))

    async def check_publish_status(self, platform_chapter_id: str) -> PublishStatus:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self._base}/writer/api/chapter/detail",
                    params={"item_id": platform_chapter_id},
                    headers=self._headers(),
                )
            data = resp.json()
            review_status = data.get("data", {}).get("review_status", "unknown")
            status_map = {0: "under_review", 1: "published", 2: "rejected"}
            mapped = status_map.get(review_status, "unknown")
            return PublishStatus(platform_chapter_id=platform_chapter_id, status=mapped)
        except httpx.HTTPError as exc:
            return PublishStatus(
                platform_chapter_id=platform_chapter_id, status="unknown", message=str(exc)
            )


# Satisfy PlatformAdapter protocol
assert isinstance(FanqieAdapter({}), PlatformAdapter)
