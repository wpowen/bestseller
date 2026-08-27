"""榜单抓取的静默空成功（2026-08-27 复发，2026-08-08 已定过一次案）。

``fetch_fanqiehub_snapshot`` 把 ``category / rank_type / platform`` 当查询参数
发出去，而接口已不再接受它们——端点照样回 200 +``{"data": [], "total": 0}``。
调用方于是拿到一个**合法的空快照**，无人报错：

    fanqie_ranking_snapshots   0 行
    fanqie_competitor_profiles 0 行

也就是说市场验证子系统一直在空转，而所有日志都是绿的。

真机实测（2026-08-27）：带旧参数 → 0 条；不带参数 → 2218 条。

修法两条：
1. **拉全量 + 本地切分**。接口只认 ``page``，而每行自带
   ``分类 / 平台 / 榜单类型``，本地过滤不会因为接口参数改名而无声失效。
2. **空响应是异常不是数据**。这是该客户端的结构性弱点，必须在类型层面堵死。
"""

from __future__ import annotations

import httpx
import pytest

from bestseller.services.fanqie_market_client import (
    FanqiehubEmptyResponseError,
    _row_matches,
    fetch_fanqiehub_rows,
    fetch_fanqiehub_snapshot,
)

pytestmark = pytest.mark.unit

_ROW = {
    "书名": "惊鸿",
    "作者": "一夕烟雨",
    "分类": "传统玄幻",
    "平台": "男频",
    "榜单类型": "阅读榜",
    "排名": 1,
    "标签": ["传统玄幻", "剑道"],
    "简介": "『传统玄幻』世间有一楼，名为烟雨楼。" * 4,
}


def _client(pages: dict[int, list[dict]]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(dict(request.url.params).get("page", 1))
        rows = pages.get(page, [])
        total = sum(len(v) for v in pages.values())
        return httpx.Response(
            200, json={"data": rows, "total": total, "per_page": 60}
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestEmptyIsAnError:
    @pytest.mark.asyncio
    async def test_zero_rows_raises_instead_of_returning_an_empty_snapshot(self):
        """★真机形状：端点 200 但零行。此前静默通过。"""
        async with _client({1: []}) as c:
            with pytest.raises(FanqiehubEmptyResponseError):
                await fetch_fanqiehub_rows(client=c)

    @pytest.mark.asyncio
    async def test_a_filter_that_matches_nothing_also_raises(self):
        """切分后为空同样要报——否则只是把静默失效挪到了本地。"""
        async with _client({1: [_ROW]}) as c:
            with pytest.raises(FanqiehubEmptyResponseError):
                await fetch_fanqiehub_snapshot(category="不存在的分类", client=c)

    @pytest.mark.asyncio
    async def test_vacuity_a_silently_empty_snapshot_would_have_looked_fine(self):
        """空转检验：旧行为返回的是合法对象，字段齐全、books 为空——
        正因为它「看起来正常」，这个洞才能埋住整个子系统。"""
        from bestseller.services.fanqie_market_client import (
            normalize_fanqiehub_snapshot,
        )

        snap = normalize_fanqiehub_snapshot(
            {"data": []}, board_type="阅读榜", category="传统玄幻", channel="男频"
        )
        assert snap.books == [] and snap.category == "传统玄幻"


class TestFetchAllThenFilterLocally:
    @pytest.mark.asyncio
    async def test_pagination_collects_every_page(self):
        async with _client({1: [_ROW] * 60, 2: [_ROW] * 5}) as c:
            rows = await fetch_fanqiehub_rows(client=c)
        assert len(rows) == 65

    @pytest.mark.asyncio
    async def test_no_filter_params_are_sent_to_the_endpoint(self):
        """接口不再接受 category/rank_type/platform——发出去就是无声失效。"""
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(dict(request.url.params))
            return httpx.Response(
                200, json={"data": [_ROW], "total": 1, "per_page": 60}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await fetch_fanqiehub_snapshot(
                category="传统玄幻", channel="男频", client=c
            )
        for params in seen:
            assert set(params) <= {"page"}, f"不该发出的参数: {params}"

    @pytest.mark.asyncio
    async def test_the_slice_keeps_the_blurb_and_tags(self):
        """简介与标签是后续创意分析的原料，切分不得丢字段。"""
        async with _client({1: [_ROW]}) as c:
            snap = await fetch_fanqiehub_snapshot(category="传统玄幻", client=c)
        assert len(snap.books) == 1
        assert len(snap.books[0].intro) > 50
        assert snap.books[0].tags


class TestLocalMatching:
    def test_empty_filter_means_no_filter(self):
        assert _row_matches(_ROW, category="", board_type="", channel="")

    def test_each_dimension_filters(self):
        assert _row_matches(_ROW, category="传统玄幻", board_type="", channel="男频")
        assert not _row_matches(_ROW, category="", board_type="新书榜", channel="")
        assert not _row_matches(_ROW, category="", board_type="", channel="女频")
