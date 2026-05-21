from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from bestseller.services.fanqie_market_client import (
    normalize_fanqiehub_book,
    normalize_fanqiehub_snapshot,
)

pytestmark = pytest.mark.unit


def test_normalize_fanqiehub_book_parses_chinese_keys() -> None:
    book = normalize_fanqiehub_book(
        {
            "排名": 1,
            "书ID": "733915888444",
            "书名": "我不是戏神",
            "作者": "三九音域",
            "分类": "都市高武",
            "平台": "番茄小说",
            "榜单类型": "阅读榜",
            "在读人数": "192.3万人",
            "标签": "都市高武、热血, 群像",
            "字数": "216.5万字",
            "简介": "少年被卷入神秘舞台。",
        }
    )

    assert book.source_book_id == "733915888444"
    assert book.title == "我不是戏神"
    assert book.rank == 1
    assert book.reader_count == 1_923_000
    assert book.word_count == 2_165_000
    assert book.tags == ["都市高武", "热血", "群像"]


def test_normalize_fanqiehub_snapshot_handles_nested_data() -> None:
    snapshot = normalize_fanqiehub_snapshot(
        {
            "data_date": "2026-05-20",
            "category": "都市脑洞",
            "board_type": "reading",
            "data": {
                "items": [
                    {
                        "rank": "2",
                        "book_id": "b2",
                        "title": "第二本",
                        "author": "作者乙",
                        "readers": "8.5万",
                    },
                    {
                        "rank": "1",
                        "book_id": "b1",
                        "title": "第一本",
                        "author": "作者甲",
                        "reader_count": 100000,
                    },
                ]
            },
        },
        fetched_at=datetime(2026, 5, 20, tzinfo=UTC),
        source_url="https://www.fanqiehub.com/api/data?category=都市脑洞",
    )

    assert snapshot.category == "都市脑洞"
    assert snapshot.data_date == date(2026, 5, 20)
    assert snapshot.sample_size == 2
    assert snapshot.top_titles == ["第一本", "第二本"]
    assert snapshot.books[1].reader_count == 85_000


def test_normalize_fanqiehub_snapshot_accepts_list_payload() -> None:
    snapshot = normalize_fanqiehub_snapshot(
        [
            {
                "数据日期": "2026-05-20",
                "排名": 1,
                "书名": "榜首",
                "作者": "匿名",
                "分类": "玄幻脑洞",
            }
        ],
        category="玄幻脑洞",
    )

    assert snapshot.data_date == date(2026, 5, 20)
    assert snapshot.books[0].source_book_id == "榜首-匿名"
