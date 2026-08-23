"""简介逻辑病三新轴：机制矛盾 / 无锚指代 / 论据不撑论点 —— 教学+留痕，不杀候选。

2026-08-23 真机（custom-xuanhuan-1787407568 的 v0 fallback 简介）：
「判官笔每划一下就吸寡妇一口气」×「往自己掌心又划了一道（救她）」的机制
自噬、「三个比宋礼年早动手」的无锚比较、「庙里没人想让他活」+ 三条与他
生死无关的贪腐例证——用户读得出「逻辑不符合语句不通顺」，而现有全部量具
零发现：确定性病理 0 条、引文核对判官 0 条（其 schema 双引文必填，单引文
的无锚指代**结构上不可表达**）。

修：扩三类 kind。按「新检测器只挣重生和留痕」规矩，新轴 is_fatal=False：
进打磨反馈与审计痕迹，不参与候选出局；出局权仍只归零冤案的原四类。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
from bestseller.services.blurb_coherence_judge import (
    CoherenceFinding,
    CoherenceReport,
    build_axis_prosecution_messages,
    build_coherence_messages,
    parse_and_verify,
)

_SYN = (
    "小道士宋礼年，手里那支判官笔每划一下就吸寡妇一口气。"
    "庙里没人想让他活：管香火的把寡妇当货卖。"
    "三个比宋礼年早动手。"
    "宋礼年抬起判官笔往自己掌心又划了一道。"
)


def test_dangling_kind_accepts_single_quote() -> None:
    raw = (
        '{"contradictions": [{"kind": "dangling", '
        '"quote_a": "三个比宋礼年早动手", "quote_b": "", '
        '"why": "动手做什么，全文没有着落"}]}'
    )
    findings, dropped = parse_and_verify(raw, source_texts=(_SYN,))
    assert dropped == 0
    assert len(findings) == 1
    assert findings[0].kind == "dangling"
    assert findings[0].is_fatal is False


def test_non_dangling_kind_still_requires_both_quotes() -> None:
    raw = (
        '{"contradictions": [{"kind": "fact", '
        '"quote_a": "三个比宋礼年早动手", "quote_b": "", "why": "x"}]}'
    )
    findings, dropped = parse_and_verify(raw, source_texts=(_SYN,))
    assert findings == ()
    assert dropped == 1


def test_mechanism_and_claim_kinds_verify_with_two_quotes() -> None:
    raw = (
        '{"contradictions": ['
        '{"kind": "mechanism", "quote_a": "每划一下就吸寡妇一口气", '
        '"quote_b": "往自己掌心又划了一道", '
        '"why": "按设定救人这一划又在吸被救者的气"},'
        '{"kind": "claim_unsupported", "quote_a": "庙里没人想让他活", '
        '"quote_b": "管香火的把寡妇当货卖", '
        '"why": "例证是贪腐，与要他死无关"}]}'
    )
    findings, dropped = parse_and_verify(raw, source_texts=(_SYN,))
    assert dropped == 0
    assert {f.kind for f in findings} == {"mechanism", "claim_unsupported"}
    assert all(f.is_fatal is False for f in findings)


def test_original_kinds_stay_fatal() -> None:
    f = CoherenceFinding(kind="timeline", quote_a="a", quote_b="b", explanation="x")
    assert f.is_fatal is True
    report = CoherenceReport(
        findings=(
            f,
            CoherenceFinding(kind="dangling", quote_a="c", quote_b="", explanation="y"),
        ),
        llm_used=True,
    )
    assert len(report.synopsis_findings) == 2
    assert len(report.fatal_synopsis_findings) == 1
    assert report.fatal_synopsis_findings[0].kind == "timeline"


def test_dangling_hallucinated_quote_still_dropped() -> None:
    raw = (
        '{"contradictions": [{"kind": "dangling", '
        '"quote_a": "这句话不在原文里出现过半个字", "quote_b": "", "why": "x"}]}'
    )
    findings, dropped = parse_and_verify(raw, source_texts=(_SYN,))
    assert findings == ()
    assert dropped == 1


def test_new_kinds_live_in_axis_prosecutors_not_the_omnibus_call() -> None:
    # A/B 实测：七类塞一个 prompt = 注意力稀释（病稿3轮中1、对照稿被冤2）。
    # 新轴必须走每轴独立检察官；大杂烩调用保持验证过的四类窄任务。
    omnibus, _user = build_coherence_messages(synopsis=_SYN)
    for token in ("mechanism", "dangling", "claim_unsupported"):
        assert token not in omnibus
    assert "不要硬凑" in omnibus
    for axis in ("mechanism", "dangling", "claim_unsupported"):
        system, user = build_axis_prosecution_messages(axis, synopsis=_SYN)
        assert axis in system
        # 每个检察官必须带反例边界（防冤案）与反硬凑护栏。
        assert "不算病" in system
        assert "不要硬凑" in system
        assert _SYN[:12] in user


def test_to_dict_carries_fatal_flag() -> None:
    adv = CoherenceFinding(kind="mechanism", quote_a="a", quote_b="b", explanation="x")
    assert adv.to_dict()["fatal"] is False
    ftl = CoherenceFinding(kind="number", quote_a="a", quote_b="b", explanation="x")
    assert ftl.to_dict()["fatal"] is True
