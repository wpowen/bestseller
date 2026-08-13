"""FanqieHub adapter: Fanqie's daily full ranking dataset in one request.

2026-08-08 verified contract: ``GET {base}/api/data?per_page=3000`` returns
every (channel × category) board for the current data date — 阅读榜 + 新书榜,
男频 + 女频, with reader counts, deltas and intros. The old per-category query
params (``platform=fanqie``, ``rank_type=reading``) are dead; filtering happens
client-side on the returned rows.
"""

# ruff: noqa: RUF002 — Chinese market vocabulary is intentional.
from __future__ import annotations

import logging

import httpx

from bestseller.domain.market_validation import MarketBookObservation
from bestseller.services.market_validation.adapters._common import (
    BROWSER_USER_AGENT,
    first_value,
    parse_chinese_number,
)
from bestseller.services.market_validation.config import SourceConfig

logger = logging.getLogger(__name__)


def normalize_fanqiehub_row(row: dict) -> MarketBookObservation | None:
    """Normalize one FanqieHub row; unusable rows become ``None``."""

    if not isinstance(row, dict):
        return None
    title = str(first_value(row, "书名", "title") or "").strip()
    if not title:
        return None
    heat_delta_raw = row.get("在读人数变化")
    rank_delta_raw = row.get("排名变化")
    tags = row.get("标签") or row.get("平台标签") or []
    if isinstance(tags, str):
        tags = [item for item in tags.replace("、", ",").split(",") if item.strip()]
    try:
        return MarketBookObservation(
            platform="fanqie",
            source_book_id=str(first_value(row, "书ID", "book_id") or ""),
            title=title,
            author=str(first_value(row, "作者", "author") or ""),
            channel=str(first_value(row, "平台", "channel") or ""),
            category=str(first_value(row, "分类", "category") or ""),
            board_type=str(first_value(row, "榜单类型", "board_type") or ""),
            rank=max(0, int(parse_chinese_number(row.get("排名", 0)))),
            heat=max(0, parse_chinese_number(first_value(row, "在读人数_数值", "在读人数"))),
            heat_label=str(row.get("在读人数") or ""),
            heat_delta=(
                int(float(heat_delta_raw)) if heat_delta_raw not in (None, "") else None
            ),
            rank_delta=(
                int(float(rank_delta_raw)) if rank_delta_raw not in (None, "") else None
            ),
            is_new_entry=bool(row.get("新入榜", False)),
            word_count=(
                parse_chinese_number(row.get("字数")) or None
                if row.get("字数") not in (None, "")
                else None
            ),
            status=str(row.get("状态") or ""),
            intro=str(row.get("简介") or "")[:4000],
            tags=[str(tag).strip() for tag in tags if str(tag).strip()],
        )
    except Exception:
        logger.debug("Skipping unparseable FanqieHub row", exc_info=True)
        return None


async def fetch_fanqiehub_dataset(
    source: SourceConfig,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[list[MarketBookObservation], str]:
    """Fetch the full current dataset. Returns (observations, data_date).

    Fail-open: any error returns ``([], "")``.
    """

    url = f"{source.base_url.rstrip('/')}/api/data"
    params = {"per_page": "3000"}
    headers = {"User-Agent": BROWSER_USER_AGENT}
    try:
        if client is None:
            async with httpx.AsyncClient(
                timeout=source.timeout_seconds, headers=headers
            ) as owned:
                response = await owned.get(url, params=params)
        else:
            response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        logger.warning("FanqieHub fetch failed; genre heat will degrade", exc_info=True)
        return [], ""

    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return [], ""
    observations = [
        obs for obs in (normalize_fanqiehub_row(row) for row in rows) if obs is not None
    ]
    data_date = ""
    for row in rows:
        if isinstance(row, dict) and row.get("数据日期"):
            data_date = str(row["数据日期"])
            break
    return observations, data_date


def filter_fanqie_observations(
    observations: list[MarketBookObservation],
    *,
    channel: str,
    category_labels: list[str] | tuple[str, ...],
    board_types: tuple[str, ...] = (),
) -> list[MarketBookObservation]:
    """Client-side board filter (channel + category, optionally board type)."""

    wanted = {label.strip() for label in category_labels if str(label).strip()}
    result = [
        obs
        for obs in observations
        if obs.channel == channel
        and obs.category in wanted
        and (not board_types or obs.board_type in board_types)
    ]
    return sorted(result, key=lambda obs: (obs.category, obs.board_type, obs.rank))
