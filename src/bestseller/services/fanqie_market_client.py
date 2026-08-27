"""Fanqie public ranking fetch and normalization helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
import re
from typing import Any

import httpx

from bestseller.domain.fanqie_market import FanqieRankingBook, FanqieRankingSnapshot

FANQIEHUB_BASE_URL = "https://www.fanqiehub.com"


def normalize_fanqiehub_book(
    raw: Mapping[str, Any],
    *,
    default_category: str = "",
    default_board_type: str = "reading",
    default_channel: str = "fanqie",
    source_url: str = "",
) -> FanqieRankingBook:
    """Normalize one FanqieHub book row into the internal ranking contract."""

    title = _first_str(raw, "书名", "title", "book_name", "name")
    source_book_id = _first_str(raw, "书ID", "book_id", "id", "bookId") or _stable_book_id(
        title=title,
        author=_first_str(raw, "作者", "author"),
    )
    reader_label = _first_str(raw, "在读人数", "readers_label", "reader_count_label")
    return FanqieRankingBook(
        source_book_id=source_book_id,
        title=title,
        author=_first_str(raw, "作者", "author"),
        category=_first_str(raw, "分类", "category", default=default_category),
        channel=_first_str(raw, "平台", "channel", "platform", default=default_channel),
        board_type=_first_str(
            raw,
            "榜单类型",
            "board_type",
            "rank_type",
            default=default_board_type,
        ),
        rank=_first_int(raw, "排名", "rank", "ranking", default=1),
        reader_count=_first_int(
            raw,
            "在读人数_数值",
            "reader_count",
            "readers",
            "热度",
            default=_parse_chinese_number(reader_label),
        ),
        reader_count_label=reader_label,
        tags=_first_list(raw, "标签", "平台标签", "tags", "tag_list"),
        status=_first_str(raw, "状态", "status"),
        latest_chapter=_first_str(raw, "最新章节", "latest_chapter", "latestChapter"),
        word_count=_optional_int(raw, "字数", "word_count", "words"),
        intro=_first_str(raw, "简介", "intro", "description", "summary"),
        source_url=source_url,
        raw=dict(raw),
    )


def normalize_fanqiehub_snapshot(
    payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    board_type: str = "reading",
    category: str = "",
    channel: str = "fanqie",
    source_url: str = "",
    fetched_at: datetime | None = None,
    data_date: date | str | None = None,
) -> FanqieRankingSnapshot:
    """Normalize a FanqieHub API payload into a ranking snapshot."""

    payload_mapping = payload if isinstance(payload, Mapping) else {}
    rows = _extract_rows(payload)
    resolved_category = _first_str(payload_mapping, "分类", "category", default=category)
    resolved_board_type = _first_str(payload_mapping, "榜单类型", "board_type", default=board_type)
    resolved_channel = _first_str(payload_mapping, "平台", "platform", "channel", default=channel)
    row_date = _first_value(rows[0], "数据日期", "data_date", "date") if rows else None
    resolved_date = _parse_date(
        data_date
        or _first_value(payload_mapping, "数据日期", "data_date", "date", "updated_date")
        or row_date
    )
    books = [
        normalize_fanqiehub_book(
            row,
            default_category=resolved_category,
            default_board_type=resolved_board_type,
            default_channel=resolved_channel,
            source_url=source_url,
        )
        for row in rows
    ]
    raw_payload = dict(payload_mapping) if isinstance(payload, Mapping) else {"data": list(payload)}
    return FanqieRankingSnapshot(
        source="fanqiehub",
        source_url=source_url,
        board_type=resolved_board_type,
        category=resolved_category,
        channel=resolved_channel,
        data_date=resolved_date,
        fetched_at=fetched_at or datetime.now(UTC),
        books=books,
        raw_payload=raw_payload,
    )


class FanqiehubEmptyResponseError(RuntimeError):
    """端点返回 200 但零行——**必须报错，不许当成空快照咽下去**。

    2026-08-08 本仓库已为此定过一次案（旧参数 200 空响应静默失效），
    2026-08-27 复发：``fetch_fanqiehub_snapshot`` 传的
    ``category / rank_type / platform`` 已不被 API 接受，端点照样回 200 +
    ``{"data": [], "total": 0}``。于是 ``fanqie_ranking_snapshots`` 一直是
    空表、``fanqie_competitor_profiles`` 0 行——**市场验证子系统空转，
    而没有任何一处报错**。

    「静默空成功」是这个客户端的结构性弱点：接口参数一变就无声失效。
    所以空响应在这里是**异常**，不是数据。
    """


async def fetch_fanqiehub_rows(
    *,
    base_url: str = FANQIEHUB_BASE_URL,
    client: httpx.AsyncClient | None = None,
    max_pages: int = 60,
) -> list[Mapping[str, Any]]:
    """拉全量榜单行（分页），不传任何过滤参数。

    过滤改在本地做：接口只认 ``page``，而每行自带 ``分类 / 平台 / 榜单类型``，
    本地切分既准确又不会因为接口参数改名而无声失效。
    """

    async def _pull(c: httpx.AsyncClient) -> list[Mapping[str, Any]]:
        first = await c.get(f"{base_url.rstrip('/')}/api/data", params={"page": 1})
        first.raise_for_status()
        head = first.json()
        rows = list(head.get("data") or [])
        total = int(head.get("total") or 0)
        per = int(head.get("per_page") or len(rows) or 1)
        pages = min(max_pages, (total + per - 1) // per if per else 1)
        for page in range(2, pages + 1):
            resp = await c.get(
                f"{base_url.rstrip('/')}/api/data", params={"page": page}
            )
            resp.raise_for_status()
            rows.extend(resp.json().get("data") or [])
        return rows

    if client is None:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as owned:
            rows = await _pull(owned)
    else:
        rows = await _pull(client)
    if not rows:
        raise FanqiehubEmptyResponseError(
            "FanqieHub returned zero rows; the endpoint contract likely changed."
        )
    return rows


def _row_matches(
    row: Mapping[str, Any], *, category: str, board_type: str, channel: str
) -> bool:
    """本地切分。空串表示不过滤该维度。"""

    def _eq(field_names: tuple[str, ...], wanted: str) -> bool:
        if not wanted:
            return True
        actual = _first_str(row, *field_names)
        return wanted in actual or actual in wanted

    return (
        _eq(("分类", "category"), category)
        and _eq(("榜单类型", "board_type", "rank_type"), board_type)
        and _eq(("平台", "channel", "platform"), channel)
    )


async def fetch_fanqiehub_snapshot(
    *,
    category: str,
    board_type: str = "",
    channel: str = "",
    base_url: str = FANQIEHUB_BASE_URL,
    client: httpx.AsyncClient | None = None,
) -> FanqieRankingSnapshot:
    """Fetch one FanqieHub slice.

    2026-08-27 改为「拉全量 + 本地切分」。此前把 ``category/rank_type/platform``
    当查询参数发出去，而接口已不再接受它们——返回 200 + 空数组，调用方拿到
    一个合法的空快照，无人报错。现在过滤在本地做，且空响应直接抛
    :class:`FanqiehubEmptyResponseError`。

    ``board_type`` / ``channel`` 默认空串＝不过滤（接口取值是中文的
    「阅读榜/新书榜」「男频/女频」，与旧的 ``reading``/``fanqie`` 不同源，
    默认不过滤避免又一次无声全滤光）。
    """

    rows = await fetch_fanqiehub_rows(base_url=base_url, client=client)
    picked = [
        row
        for row in rows
        if _row_matches(
            row, category=category, board_type=board_type, channel=channel
        )
    ]
    if not picked:
        raise FanqiehubEmptyResponseError(
            f"no rows matched category={category!r} "
            f"board_type={board_type!r} channel={channel!r} "
            f"(fetched {len(rows)} rows)"
        )
    return normalize_fanqiehub_snapshot(
        {"data": picked},
        board_type=board_type or _first_str(picked[0], "榜单类型"),
        category=category or _first_str(picked[0], "分类"),
        channel=channel or _first_str(picked[0], "平台"),
        source_url=f"{base_url.rstrip('/')}/api/data",
    )


def _extract_rows(
    payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray, Mapping)):
        return [row for row in payload if isinstance(row, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    candidate = _first_value(payload, "data", "books", "items", "records", "list")
    if isinstance(candidate, Mapping):
        candidate = _first_value(candidate, "data", "books", "items", "records", "list")
    if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes, bytearray)):
        return [row for row in candidate if isinstance(row, Mapping)]
    return []


def _first_value(mapping: Mapping[str, Any], *keys: str) -> object | None:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _first_str(mapping: Mapping[str, Any], *keys: str, default: str = "") -> str:
    value = _first_value(mapping, *keys)
    return str(value).strip() if value not in (None, "") else default


def _first_int(mapping: Mapping[str, Any], *keys: str, default: int = 0) -> int:
    value = _first_value(mapping, *keys)
    if value in (None, ""):
        return default
    return _parse_int(value, default=default)


def _optional_int(mapping: Mapping[str, Any], *keys: str) -> int | None:
    value = _first_value(mapping, *keys)
    if value in (None, ""):
        return None
    return _parse_int(value, default=0)


def _first_list(mapping: Mapping[str, Any], *keys: str) -> list[str]:
    value = _first_value(mapping, *keys)
    if value is None:
        return []
    if isinstance(value, str):
        return [item for item in re.split(r"[\u3001,\uff0c/|;\uff1b\s]+", value) if item]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _parse_date(value: date | str | None) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        match = re.search(r"\d{4}-\d{1,2}-\d{1,2}", str(value))
        if match:
            return date.fromisoformat(match.group(0))
    return date.today()


def _parse_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    parsed = _parse_chinese_number(str(value))
    return parsed if parsed > 0 else default


def _parse_chinese_number(value: str) -> int:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return 0
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return 0
    number = float(match.group(1))
    if "亿" in text:
        number *= 100_000_000
    elif "万" in text:
        number *= 10_000
    return int(number)


def _stable_book_id(*, title: str, author: str) -> str:
    seed = f"{title}:{author}".strip(":") or "unknown"
    return re.sub(r"\W+", "-", seed).strip("-")[:128] or "unknown"
