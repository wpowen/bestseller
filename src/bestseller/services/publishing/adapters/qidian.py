from __future__ import annotations

"""起点中文网 publishing adapter.

Credentials expected keys: {"cookie": "...", "book_id": "..."}
API base: https://book.qidian.com (author backend)
"""

import logging
from typing import Any

import httpx

from bestseller.services.publishing.base import (
    ChapterPublishMeta,
    PublishResult,
    PublishStatus,
)

logger = logging.getLogger(__name__)

_DEFAULT_BASE = "https://book.qidian.com"


class QidianAdapter:
    platform_type = "qidian"

    def __init__(self, credentials: dict[str, Any], api_base_url: str | None = None) -> None:
        self._cookie = credentials.get("cookie", "")
        self._book_id = credentials.get("book_id", "")
        self._base = (api_base_url or _DEFAULT_BASE).rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Cookie": self._cookie,
            "User-Agent": "Mozilla/5.0 (compatible; BestSeller/1.0)",
            "Referer": f"{self._base}/",
        }

    async def authenticate(self) -> bool:
        if not self._cookie or not self._book_id:
            return False
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self._base}/ajax/book/detail",
                    params={"bookId": self._book_id},
                    headers=self._headers(),
                )
            return resp.status_code == 200
        except httpx.HTTPError as exc:
            logger.error("QidianAdapter.authenticate error: %s", exc)
            return False

    async def publish_chapter(
        self,
        content: str,
        meta: ChapterPublishMeta,
    ) -> PublishResult:
        payload = {
            "bookId": self._book_id,
            "chapterName": meta.title or f"第{meta.chapter_number}章",
            "content": content,
        }
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self._base}/ajax/chapter/add",
                    json=payload,
                    headers=self._headers(),
                )
            data = resp.json()
            code = data.get("code", -1)
            if resp.status_code == 200 and code == 0:
                chapter_id = str(data.get("data", {}).get("chapterId", ""))
                return PublishResult(
                    success=True,
                    platform_chapter_id=chapter_id,
                    platform_response=data,
                )
            return PublishResult(
                success=False,
                platform_response=data,
                error_message=data.get("msg", "Publish failed"),
            )
        except httpx.HTTPError as exc:
            logger.error("QidianAdapter.publish_chapter error: %s", exc)
            return PublishResult(success=False, error_message=str(exc))

    async def check_publish_status(self, platform_chapter_id: str) -> PublishStatus:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self._base}/ajax/chapter/detail",
                    params={"chapterId": platform_chapter_id},
                    headers=self._headers(),
                )
            data = resp.json()
            review = data.get("data", {}).get("reviewStatus", "unknown")
            return PublishStatus(platform_chapter_id=platform_chapter_id, status=str(review))
        except httpx.HTTPError as exc:
            return PublishStatus(
                platform_chapter_id=platform_chapter_id, status="unknown", message=str(exc)
            )
