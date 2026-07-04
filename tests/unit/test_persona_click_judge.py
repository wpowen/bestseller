"""L1 unit tests for the persona click judge (模拟目标读者「3秒点不点」).

审计 P1-6：genre_persona 的画像判官此前是死代码，构思验收只剩绝对分。这里 pin：
画像路由进 prompt、宽容 JSON 解析、多采样聚合、fail-open（LLM 全废 → advisory 放行）。
LLM 用注入的 fake judge——判官真实效果由真机脚本验证。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — Chinese fixtures.
import pytest

from bestseller.services.persona_click_judge import (
    PersonaClickReport,
    build_persona_judge_messages,
    load_persona_judge_config,
    parse_persona_click_verdict,
    run_persona_click_judge,
)


@pytest.mark.unit
def test_messages_route_male_persona_and_carry_title_blurb():
    system, user = build_persona_judge_messages(
        title="蚀骨神藏", blurb="废柴少年觉醒神藏，一路打脸碾压。", genre="玄幻",
    )
    # 男频画像角色扮演进 system（3秒点不点的读者身份）
    assert "3秒" in system or "3 秒" in system
    assert "外卖" in system  # 男频 persona_judge_role 的身份锚
    assert "蚀骨神藏" in user
    assert "废柴少年觉醒神藏" in user


@pytest.mark.unit
def test_messages_route_female_persona():
    system, _ = build_persona_judge_messages(
        title="重生后我把渣男甩了", blurb="重生回到订婚前夜。", genre="现代言情",
    )
    assert "行政" in system or "言情" in system  # 女频 persona_judge_role


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw,click,score",
    [
        ('{"click": true, "score": 8, "reason": "想看"}', True, 8.0),
        ('前置噪声 {"click": false, "score": 2.5, "reason": "看不懂"} 后缀', False, 2.5),
        ('{"click": "true", "score": "7", "reason": "ok"}', True, 7.0),
    ],
)
def test_parse_verdict_tolerant(raw, click, score):
    v = parse_persona_click_verdict(raw)
    assert v is not None
    assert v.click is click
    assert v.score == pytest.approx(score)


@pytest.mark.unit
def test_parse_verdict_garbage_returns_none_and_score_clamped():
    assert parse_persona_click_verdict("完全不是JSON") is None
    v = parse_persona_click_verdict('{"click": true, "score": 99, "reason": ""}')
    assert v is not None and v.score == 10.0


@pytest.mark.unit
async def test_run_judge_aggregates_click_rate():
    answers = iter(
        [
            '{"click": true, "score": 8, "reason": "爽点直给"}',
            '{"click": false, "score": 3, "reason": "看不懂黑话"}',
            '{"click": true, "score": 7, "reason": "有反差"}',
        ]
    )

    async def _judge(system, user):
        return next(answers)

    report = await run_persona_click_judge(
        None, None, title="书名", synopsis="简介", genre="玄幻",
        judge=_judge, config={"persona_judge": {"samples": 3, "click_rate_min": 0.34}},
    )
    assert report.llm_used is True
    assert report.samples == 3 and report.clicks == 2
    assert report.click_rate == pytest.approx(2 / 3)
    assert report.avg_score == pytest.approx(6.0)
    assert report.advisory_pass(0.34) is True
    assert report.advisory_pass(0.7) is False
    assert any("黑话" in r for r in report.reasons)


@pytest.mark.unit
async def test_run_judge_fail_open_when_all_samples_unparseable():
    async def _judge(system, user):
        return "not json at all"

    report = await run_persona_click_judge(
        None, None, title="书名", synopsis="简介", genre="玄幻",
        judge=_judge, config={"persona_judge": {"samples": 2}},
    )
    assert report.llm_used is False
    assert report.samples == 0
    # fail-open：判官不可用绝不误毙
    assert report.advisory_pass(0.34) is True


@pytest.mark.unit
async def test_run_judge_survives_judge_exception():
    calls = {"n": 0}

    async def _judge(system, user):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return '{"click": true, "score": 9, "reason": "ok"}'

    report = await run_persona_click_judge(
        None, None, title="t", synopsis="s", genre="玄幻",
        judge=_judge, config={"persona_judge": {"samples": 2}},
    )
    assert report.samples == 1 and report.clicks == 1


@pytest.mark.unit
def test_config_defaults_and_report_dict_roundtrip():
    cfg = load_persona_judge_config({})
    assert cfg["enabled"] is True
    assert cfg["samples"] >= 1
    assert 0 < cfg["click_rate_min"] <= 1
    assert cfg["block_below"] is False  # 默认 advisory，不硬拦

    r = PersonaClickReport(
        channel="男频", samples=3, clicks=2, click_rate=2 / 3,
        avg_score=6.0, reasons=("a",), llm_used=True,
    )
    d = r.to_dict()
    assert d["channel"] == "男频" and d["click_rate"] == pytest.approx(2 / 3, abs=1e-3)
    assert d["schema_version"] == "persona-click-judge.v1"
