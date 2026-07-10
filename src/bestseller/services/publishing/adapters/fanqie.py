"""番茄小说 publishing adapter.

Authentication: cookie-based session (from browser login) or API token.
Credentials expected keys: {"cookie": "...", "book_id": "..."}

NOTE: 番茄小说 does not publish a public writer API. This adapter uses the
internal web API observed from the author backend (https://fanqienovel.com/writer).
Treat as best-effort; update endpoint paths if they change.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from bestseller.services.publishing.base import (
    ChapterPublishMeta,
    PlatformAdapter,
    PublishResult,
    PublishStatus,
    is_business_auth_error,
    normalize_publish_status,
)

logger = logging.getLogger(__name__)

_DEFAULT_BASE = "https://fanqienovel.com"


class FanqieAdapter:
    platform_type = "fanqie"
    # The private writer endpoint accepts a client key but publishes no
    # contractual idempotency guarantee. Crash recovery must fail closed.
    supports_idempotency = False

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
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self._base}/writer/api/book/detail",
                params={"book_id": self._book_id},
                headers=self._headers(),
            )
        if resp.status_code == 200:
            try:
                data = resp.json()
            except (TypeError, ValueError):
                return False
            return not is_business_auth_error(data) and str(data.get("code")) == "0"
        if resp.status_code in {301, 302, 401, 403}:
            return False
        resp.raise_for_status()
        raise RuntimeError(f"Unexpected authentication response: HTTP {resp.status_code}")

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
        headers = self._headers()
        if meta.idempotency_key:
            payload["client_request_id"] = meta.idempotency_key
            headers["Idempotency-Key"] = meta.idempotency_key
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self._base}/writer/api/chapter/publish",
                    json=payload,
                    headers=headers,
                )
            try:
                data = resp.json()
            except ValueError:
                data = {}
            if resp.status_code == 200 and data.get("code") == 0:
                chapter_id = str(data.get("data", {}).get("item_id", ""))
                return PublishResult(
                    success=True,
                    platform_chapter_id=chapter_id,
                    platform_response=data,
                )
            retryable = resp.status_code == 429 or resp.status_code >= 500
            error_kind = "transient" if retryable else (
                "auth"
                if resp.status_code in {401, 403} or is_business_auth_error(data)
                else "content"
            )
            return PublishResult(
                success=False,
                platform_response=data,
                error_message=data.get("message", "Publish failed"),
                retryable=retryable,
                error_kind=error_kind,
            )
        except httpx.HTTPError as exc:
            logger.error("FanqieAdapter.publish_chapter error: %s", exc)
            retryable = isinstance(exc, httpx.PoolTimeout)
            return PublishResult(
                success=False,
                error_message=str(exc),
                retryable=retryable,
                error_kind="transient" if retryable else "delivery_unknown",
            )

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
            mapped = normalize_publish_status(review_status)
            return PublishStatus(platform_chapter_id=platform_chapter_id, status=mapped)
        except httpx.HTTPError as exc:
            return PublishStatus(
                platform_chapter_id=platform_chapter_id, status="unknown", message=str(exc)
            )


# Satisfy PlatformAdapter protocol
assert isinstance(FanqieAdapter({}), PlatformAdapter)
