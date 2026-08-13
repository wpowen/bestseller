"""Qimao adapter: official unauthenticated ranking JSON.

2026-08-08 verified contract:
``GET {base}/api/rank/book-list?is_girl={0|1}&rank_type={1..5}&date_type={1|2}&page=N``
returns 20 rows/page with title/author/categories/word count/intro plus a
``number``+``unit`` heat value and trend flags (``is_new``, ``index_change``).
rank_type: 1大热 2新书 3完结 4收藏 5更新; date_type: 1日 2月.
"""


from __future__ import annotations

import logging

import httpx

from bestseller.domain.market_validation import MarketBookObservation
from bestseller.services.market_validation.adapters._common import (
    BROWSER_USER_AGENT,
    parse_chinese_number,
)
from bestseller.services.market_validation.config import SourceConfig

logger = logging.getLogger(__name__)


def normalize_qimao_row(
    row: dict, *, channel: str, board_type: str
) -> MarketBookObservation | None:
    if not isinstance(row, dict):
        return None
    title = str(row.get("title") or "").strip()
    if not title:
        return None
    number = row.get("number")
    unit = str(row.get("unit") or "")
    heat = 0
    if number not in (None, ""):
        try:
            heat = int(float(number) * (10_000 if "万" in unit or unit == "" else 1))
        except (TypeError, ValueError):
            heat = 0
    index_change = row.get("index_change")
    try:
        return MarketBookObservation(
            platform="qimao",
            source_book_id=str(row.get("book_id") or ""),
            title=title,
            author=str(row.get("author") or ""),
            channel=channel,
            category=str(row.get("category2_name") or row.get("category1_name") or ""),
            board_type=board_type,
            heat=max(0, heat),
            heat_label=f"{number}{unit}" if number not in (None, "") else "",
            rank_delta=(
                int(float(index_change)) if index_change not in (None, "") else None
            ),
            is_new_entry=bool(int(row.get("is_new") or 0)),
            word_count=parse_chinese_number(row.get("words_num")) or None,
            status="完结" if str(row.get("is_over")) == "1" else "连载中",
            intro=str(row.get("intro") or "")[:4000],
            source_url=str(row.get("book_url") or ""),
        )
    except Exception:
        logger.debug("Skipping unparseable qimao row", exc_info=True)
        return None


async def fetch_qimao_board(
    source: SourceConfig,
    *,
    is_girl: int,
    rank_type: int,
    board_type: str,
    channel: str,
    date_type: int = 1,
    client: httpx.AsyncClient | None = None,
) -> list[MarketBookObservation]:
    """Fetch one board across ``source.rank_pages`` pages. Fail-open to []."""

    url = f"{source.base_url.rstrip('/')}/api/rank/book-list"
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Referer": f"{source.base_url.rstrip('/')}/paihang/",
    }
    observations: list[MarketBookObservation] = []
    owned_client = client is None
    active = client or httpx.AsyncClient(timeout=source.timeout_seconds)
    try:
        for page in range(1, source.rank_pages + 1):
            params = {
                "is_girl": str(is_girl),
                "rank_type": str(rank_type),
                "date_type": str(date_type),
                "page": str(page),
            }
            try:
                response = await active.get(url, params=params, headers=headers)
                response.raise_for_status()
                payload = response.json()
            except Exception:
                logger.warning(
                    "Qimao fetch failed (page %s); returning partial", page,
                    exc_info=True,
                )
                break
            rows = (payload.get("data") or {}).get("table_data") or []
            if not rows:
                break
            for row in rows:
                obs = normalize_qimao_row(row, channel=channel, board_type=board_type)
                if obs is not None:
                    observations.append(obs)
    finally:
        if owned_client:
            await active.aclose()

    return [
        obs.model_copy(update={"rank": position})
        for position, obs in enumerate(observations, start=1)
    ]
