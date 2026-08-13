# ruff: noqa: RUF001, RUF002, RUF003 — Chinese market vocabulary is intentional.
from __future__ import annotations

import httpx
import pytest

from bestseller.domain.market_validation import (
    MarketSectionStatus,
    MarketValidationRequest,
)
from bestseller.services.market_validation.config import (
    load_market_validation_config,
    reset_market_validation_config_cache,
)
from bestseller.services.market_validation.service import run_market_validation

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _fresh_cache():
    reset_market_validation_config_cache()
    yield
    reset_market_validation_config_cache()


def _fanqie_row(title: str, category: str = "东方仙侠", **kwargs) -> dict:
    row = {
        "书ID": f"id-{title}",
        "书名": title,
        "作者": "作者",
        "分类": category,
        "在读人数_数值": 150_000,
        "在读人数变化": 1_000,
        "平台": "男频",
        "排名": 1,
        "数据日期": "2026-08-08",
        "新入榜": False,
        "榜单类型": "阅读榜",
        "状态": "连载中",
        "简介": "少年修行，砍翻天下。这是一段足够长的简介，用来参与形态统计。",
    }
    row.update(kwargs)
    return row


def _mock_transport() -> httpx.MockTransport:
    fanqie_rows = [
        _fanqie_row(f"仙侠书{i}", 排名=i + 1, 新入榜=(i < 3)) for i in range(15)
    ]
    fanqie_rows.append(_fanqie_row("十日终焉", category="悬疑脑洞"))
    qimao_rows = [
        {
            "book_id": f"q{i}",
            "title": f"七猫仙侠{i}",
            "author": "作者",
            "category1_name": "仙侠",
            "category2_name": "古典仙侠",
            "words_num": "100万字",
            "intro": "七猫简介。",
            "number": 50.0,
            "unit": "万",
            "is_new": 0,
            "index_change": 0,
            "is_over": "0",
            "book_url": f"https://www.qimao.com/shuku/q{i}/",
        }
        for i in range(5)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if "api/data" in str(request.url):
            return httpx.Response(200, json={"data": fanqie_rows})
        if "api/rank/book-list" in str(request.url):
            page = int(request.url.params.get("page", "1"))
            rows = qimao_rows if page == 1 else []
            return httpx.Response(200, json={"data": {"table_data": rows}})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_full_run_offline_no_llm() -> None:
    """无 LLM、无 web 检索：全确定性路径出完整报告。"""

    request = MarketValidationRequest(
        genre_key="xianxia",
        genre_label="仙侠",
        concept="少年获得可以透支寿命的剑诀",
        title_candidates=("十日终焉", "焚天问道录"),
        blurb="少年修行，砍翻天下。少年修行，砍翻天下。",
    )
    async with httpx.AsyncClient(transport=_mock_transport()) as client:
        report = await run_market_validation(request, http_client=client)

    assert set(report.platforms_used) == {"fanqie", "qimao"}
    assert report.data_dates["fanqie"] == "2026-08-08"

    assert report.genre_heat.status == MarketSectionStatus.OK
    assert report.genre_heat.sample_size == 15
    assert report.genre_heat.heat_p50 == 150_000
    assert report.genre_heat.notes  # 七猫交叉验证注记

    # 「十日终焉」在番茄榜上（悬疑脑洞）——跨分类查重也要抓到
    finding_map = {f.candidate: f for f in report.title_check.findings}
    assert finding_map["十日终焉"].verdict == "fail"
    assert finding_map["焚天问道录"].verdict == "pass"

    # 无 LLM：撞车扫描降级但竞品列表仍在
    assert report.competitor_scan.status == MarketSectionStatus.DEGRADED
    assert report.competitor_scan.competitors

    assert report.blurb_benchmark.status == MarketSectionStatus.OK
    assert report.verdict.status == MarketSectionStatus.OK
    assert 5 <= report.verdict.score <= 95

    summary = report.summary()
    assert summary["verdict"]["band"] in {"go", "revise", "no_go"}


@pytest.mark.asyncio
async def test_disabled_config_makes_no_requests(tmp_path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    path = tmp_path / "off.yaml"
    path.write_text("version: 1\nenabled: false\n", encoding="utf-8")
    config = load_market_validation_config(path)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await run_market_validation(
            MarketValidationRequest(genre_key="xianxia"),
            config=config,
            http_client=client,
        )

    assert calls == []
    assert report.genre_heat.status == MarketSectionStatus.SKIPPED


@pytest.mark.asyncio
async def test_dead_sources_degrade_not_raise() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    request = MarketValidationRequest(
        genre_key="xianxia", title_candidates=("随便一个名",)
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await run_market_validation(request, http_client=client)

    assert report.platforms_used == []
    assert report.genre_heat.status == MarketSectionStatus.DEGRADED
    assert report.title_check.status == MarketSectionStatus.DEGRADED
    assert report.verdict.status == MarketSectionStatus.OK  # 打分兜底仍给结论


@pytest.mark.asyncio
async def test_unmapped_genre_skips_heat_but_title_check_runs() -> None:
    request = MarketValidationRequest(
        genre_key="pure-love", channel="female", title_candidates=("焚天问道录",)
    )
    async with httpx.AsyncClient(transport=_mock_transport()) as client:
        report = await run_market_validation(request, http_client=client)

    assert report.genre_heat.status == MarketSectionStatus.SKIPPED
    assert report.title_check.status == MarketSectionStatus.OK
