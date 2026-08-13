"""画像判官补两根「追读侧」观测量：surprise / aversion。

真机 custom-xuanhuan-1786023406（2026-08-07）：标题 83.2、简介 72.5、
click_rate 1.0、avg_score 8.67、arena 0.50、LLM 判官 0.78——**每一道关都合法
通过**，而人读完的评价是「一看名字就知道剧情」「感觉不到爽感」「有点恶心」。

判官的三条点击理由分别是「这套路太爽了」「套路太对胃口了」「直接戳中爽点」：
它在为**可预测性**点赞。点击判断本身没错（书城 3 秒点击就是靠套路识别驱动），
错在整套体系里没有任何一处 measure「点进去之后」——猜不猜得到、看着恶不恶心。

这两维和 click 同一次调用产出（零额外成本），**先只上报不设门**：
``advisory_pass`` 仍只看 click_rate，阈值要拿真实爆款校准过才能生效。
"""

from __future__ import annotations

from bestseller.services.persona_click_judge import (
    PersonaClickReport,
    parse_persona_click_verdict,
)


def test_parses_the_two_new_axes() -> None:
    v = parse_persona_click_verdict(
        '{"click": true, "score": 8.5, "surprise": 2, "aversion": 7,'
        ' "reason": "套路太对胃口"}'
    )
    assert v is not None
    assert v.click is True
    assert v.surprise == 2.0
    assert v.aversion == 7.0


def test_missing_axes_are_unmeasured_not_zero() -> None:
    # 关键区分：模型没给 ≠ 「毫无惊喜/毫无不适」。0 是有意义的真值，
    # 缺失必须是 -1，否则老模型的沉默会被统计成「全书零惊喜」。
    v = parse_persona_click_verdict('{"click": true, "score": 8.5, "reason": "好"}')
    assert v is not None
    assert v.surprise == -1.0
    assert v.aversion == -1.0


def test_unparseable_axis_is_unmeasured() -> None:
    v = parse_persona_click_verdict(
        '{"click": false, "score": 3, "surprise": "很难说", "reason": "看不懂"}'
    )
    assert v is not None
    assert v.surprise == -1.0


def test_axes_are_clamped_to_0_10() -> None:
    v = parse_persona_click_verdict(
        '{"click": true, "score": 5, "surprise": 99, "aversion": -4, "reason": "x"}'
    )
    assert v is not None
    assert v.surprise == 10.0
    assert v.aversion == 0.0


def test_click_parsing_is_unchanged_by_the_addition() -> None:
    # no-op 契约：不带新字段的旧响应，行为必须与加这两维之前逐字节一致。
    v = parse_persona_click_verdict('{"click": "会点", "score": 9, "reason": "爽"}')
    assert v is not None
    assert v.click is True
    assert v.score == 9.0
    assert v.reason == "爽"


def test_new_axes_do_not_affect_advisory_pass() -> None:
    # 一本「全能猜到 + 强烈反胃」但点击率达标的书，仍必须放行——
    # 阈值未校准前不许拿这两维毙书。
    r = PersonaClickReport(
        channel="男频", samples=3, clicks=3, click_rate=1.0, avg_score=8.67,
        reasons=("套路太爽了",), llm_used=True,
        avg_surprise=0.5, avg_aversion=9.0,
    )
    assert r.advisory_pass(0.34) is True


def test_report_surfaces_the_axes_for_calibration() -> None:
    r = PersonaClickReport(
        channel="男频", samples=3, clicks=3, click_rate=1.0, avg_score=8.67,
        reasons=(), llm_used=True, avg_surprise=1.333, avg_aversion=6.667,
    )
    d = r.to_dict()
    assert d["avg_surprise"] == 1.33
    assert d["avg_aversion"] == 6.67


def test_report_defaults_keep_old_construction_working() -> None:
    # 老调用点不传新字段也要能构造（向后兼容），且落库为「未测量」。
    r = PersonaClickReport(
        channel="通用", samples=1, clicks=0, click_rate=0.0, avg_score=2.0,
        reasons=("看不懂",), llm_used=True,
    )
    assert r.to_dict()["avg_surprise"] == -1.0
    assert r.to_dict()["avg_aversion"] == -1.0


def test_prompt_asks_both_questions() -> None:
    from bestseller.services.persona_click_judge import build_persona_judge_messages

    system, _ = build_persona_judge_messages(
        title="捡废虫卵喂破碗", blurb="他是杂役。", genre="xuanhuan",
    )
    assert "surprise" in system and "aversion" in system
    # 必须明说「熟悉的套路=猜得到=低分」，否则模型会把套路当优点打高分——
    # 这正是真机三条理由全在夸套路的由来。
    assert "套路" in system
