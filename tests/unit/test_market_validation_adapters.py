# ruff: noqa: RUF001 — Chinese market vocabulary is intentional.
from __future__ import annotations

import httpx
import pytest

from bestseller.services.market_validation.adapters.fanqiehub import (
    fetch_fanqiehub_dataset,
    filter_fanqie_observations,
    normalize_fanqiehub_row,
)
from bestseller.services.market_validation.adapters.qimao import (
    fetch_qimao_board,
    normalize_qimao_row,
)
from bestseller.services.market_validation.config import (
    SourceConfig,
    reset_market_validation_config_cache,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _fresh_cache():
    reset_market_validation_config_cache()
    yield
    reset_market_validation_config_cache()


FANQIEHUB_ROW = {
    "书ID": "7320218217488600126",
    "书名": "领主：我在苦痛世界，养成少女",
    "作者": "嘎嘎乱写",
    "分类": "西方奇幻",
    "在读人数": "44.7万",
    "在读人数_数值": 447000.0,
    "在读人数变化": -2000.0,
    "字数": 0,
    "平台": "男频",
    "排名": 1,
    "排名变化": 0,
    "数据日期": "2026-08-08",
    "新入榜": False,
    "是否新书": 0,
    "最新章节": "第 1850章 晨曦vs精灵（三）",
    "榜单类型": "阅读榜",
    "状态": "连载中",
    "标签": ["西方奇幻", "奇幻仙侠"],
    "简介": "穿越中世纪，成为贵族。",
}

QIMAO_ROW = {
    "book_id": "195958",
    "title": "盖世神医",
    "author": "狐颜乱语",
    "category1_name": "都市",
    "category2_name": "都市高武",
    "is_over": "0",
    "words_num": "888.84万字",
    "intro": "任你权势滔天。",
    "update_time": "2026-08-08 13:11:29",
    "number": 137.0,
    "unit": "万",
    "is_new": 0,
    "index_change": 2,
    "book_url": "https://www.qimao.com/shuku/195958/",
}


class TestFanqiehubNormalize:
    def test_row_maps_to_observation(self) -> None:
        obs = normalize_fanqiehub_row(FANQIEHUB_ROW)

        assert obs is not None
        assert obs.platform == "fanqie"
        assert obs.channel == "男频"
        assert obs.category == "西方奇幻"
        assert obs.board_type == "阅读榜"
        assert obs.heat == 447000
        assert obs.heat_delta == -2000
        assert obs.rank == 1
        assert obs.is_new_entry is False
        assert obs.intro.startswith("穿越中世纪")

    def test_new_entry_flag(self) -> None:
        row = dict(FANQIEHUB_ROW, 新入榜=True)

        obs = normalize_fanqiehub_row(row)

        assert obs is not None and obs.is_new_entry is True

    def test_garbage_row_returns_none(self) -> None:
        assert normalize_fanqiehub_row({"随便": 1}) is None


class TestFanqiehubFetch:
    @pytest.mark.asyncio
    async def test_fetch_and_filter(self) -> None:
        payload = {
            "data": [
                FANQIEHUB_ROW,
                dict(FANQIEHUB_ROW, 书名="别的分类", 分类="东方仙侠", 书ID="2"),
                dict(FANQIEHUB_ROW, 书名="女频书", 平台="女频", 分类="快穿", 书ID="3"),
            ],
            "total": 3,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["per_page"] == "3000"
            return httpx.Response(200, json=payload)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            observations, data_date = await fetch_fanqiehub_dataset(
                SourceConfig(enabled=True, base_url="https://hub.test"),
                client=client,
            )

        assert len(observations) == 3
        assert data_date == "2026-08-08"

        filtered = filter_fanqie_observations(
            observations, channel="男频", category_labels=["东方仙侠"]
        )
        assert [obs.title for obs in filtered] == ["别的分类"]


class TestQimaoNormalize:
    def test_row_maps_to_observation(self) -> None:
        obs = normalize_qimao_row(QIMAO_ROW, channel="男频", board_type="大热榜")

        assert obs is not None
        assert obs.platform == "qimao"
        assert obs.channel == "男频"
        assert obs.category == "都市高武"
        assert obs.heat == 1_370_000  # 137.0万
        assert obs.word_count == 8_888_400
        assert obs.rank_delta == 2
        assert obs.source_url.endswith("/195958/")

    def test_missing_number_defaults_zero(self) -> None:
        row = {k: v for k, v in QIMAO_ROW.items() if k not in ("number", "unit")}

        obs = normalize_qimao_row(row, channel="男频", board_type="大热榜")

        assert obs is not None and obs.heat == 0


class TestQimaoFetch:
    @pytest.mark.asyncio
    async def test_fetch_pages_and_rank_assignment(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            page = int(request.url.params["page"])
            rows = [
                dict(QIMAO_ROW, book_id=f"{page}-{i}", title=f"书{page}-{i}")
                for i in range(2)
            ]
            return httpx.Response(200, json={"data": {"table_data": rows}})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            observations = await fetch_qimao_board(
                SourceConfig(enabled=True, base_url="https://qimao.test", rank_pages=2),
                is_girl=0,
                rank_type=1,
                board_type="大热榜",
                channel="男频",
                client=client,
            )

        assert len(calls) == 2
        assert len(observations) == 4
        assert [obs.rank for obs in observations] == [1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            observations = await fetch_qimao_board(
                SourceConfig(enabled=True, base_url="https://qimao.test"),
                is_girl=0,
                rank_type=1,
                board_type="大热榜",
                channel="男频",
                client=client,
            )

        assert observations == []
