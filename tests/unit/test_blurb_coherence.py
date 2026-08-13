"""简介自洽层：期限堆叠（确定性）+ 引文核对式矛盾校验（LLM 侧的纯解析部分）。

2026-08-07 真机 custom-xianxia-1786090118：对外简介同时压着四条倒计时
（一个时辰/今夜/月底/一个月），土豆先被徒弟揣走又被主角下锅，premise 三十五岁
vs spine 三十年厨房功夫。当时全链判定：comprehensibility **满分 5.0**（只数生造
黑话）、病理零命中、简介 66/consider。矛盾对每一把词表尺子都不可见。

矛盾的根源在正典：spine.why_now 一格里塞了两条期限，简介继承后又加了第三条。
所以修的是三层：① 确定性期限堆叠检测（校准：42 条真爆款中期限表达 ≥2 的为
0 条，坏简介 4 条）；② 引文核对式 LLM 矛盾扫描（模型必须逐字引出两段冲突原文，
程序核对子串，引不出=幻觉丢弃——同日已证明「问感受」得到吹捧，只能给校验任务）；
③ 三处生成 prompt 加自洽铁律 + 打磨环节补喂事实准绳。
"""

from __future__ import annotations

from bestseller.services.blurb_coherence_judge import (
    CoherenceFinding,
    CoherenceReport,
    build_coherence_messages,
    parse_and_verify,
)
from bestseller.services.blurb_pathology import detect_blurb_pathology

# 真机原文。
_BAD_BLURB = (
    "锅底下压着的，是一座漏灵气的老洞府。纪釜盘了七年盒饭铺，三十五岁，"
    "灶火只剩一个时辰的命。灵气复苏那天，他从一条鲤鱼肚里吞下一缕青光，"
    "从此洞府里埋了不知多少年的天材地宝，一样一样往他灶台上顶。"
    "隔壁醉仙楼有牌照、有靠山、有资本，限他一个月连铺带人吞干净。"
    "被骂跑的小徒弟怀里揣着一筐发光的土豆，行李都没寄走。"
    "房租月底断缴，灶火今夜就凉，他把那筐土豆下了锅。"
    "这一锅，炒的是整条街的修士，也是他自己的命。"
)


# ── ① 确定性：期限堆叠 ─────────────────────────────────────────────────


def _pileup(text: str) -> list:
    return [f for f in detect_blurb_pathology(text) if f.code == "DEADLINE_PILEUP"]


def test_real_regression_four_deadlines_is_fatal() -> None:
    found = _pileup(_BAD_BLURB)
    assert len(found) == 1
    assert found[0].severity == "fatal"
    for token in ("一个时辰", "今夜", "月底", "一个月"):
        assert token in found[0].excerpt


def test_single_deadline_is_the_normal_case() -> None:
    # 一条倒计时是好文案的标配，绝不许拦。
    assert _pileup("宗门大比只剩三天，他的丹田还是空的。") == []


def test_two_deadlines_warn_not_fatal() -> None:
    # 双期限在嵌套压力叙事里理论上合法（42 条爆款里没出现过，但不替未来封死）。
    found = _pileup("限他三天之内交出丹方，而宗门大比月底就要开场。")
    assert len(found) == 1
    assert found[0].severity == "warn"


def test_narrative_time_jump_is_not_a_deadline() -> None:
    # 「三天后他回来了」是时间跳跃不是期限压力——没有压力标记不计数。
    assert _pileup("三天后他回到宗门。一年过去，无人再提这件事。") == []


# ── ② 引文核对式矛盾解析 ────────────────────────────────────────────────


def test_verified_contradiction_survives() -> None:
    raw = (
        '{"contradictions": [{"kind": "timeline", "quote_a": "灶火只剩一个时辰的命",'
        ' "quote_b": "限他一个月连铺带人吞干净", "why": "一个时辰后灶火已熄，一个月的通牒无意义"}]}'
    )
    findings, dropped = parse_and_verify(raw, source_texts=(_BAD_BLURB,))
    assert len(findings) == 1
    assert dropped == 0
    assert findings[0].kind == "timeline"


def test_hallucinated_quote_is_dropped() -> None:
    # 整个设计的核心保险：模型引的原文不在文本里 → 幻觉，丢弃。
    raw = (
        '{"contradictions": [{"kind": "fact", "quote_a": "他早已破产三次",'
        ' "quote_b": "限他一个月连铺带人吞干净", "why": "编造的引文"}]}'
    )
    findings, dropped = parse_and_verify(raw, source_texts=(_BAD_BLURB,))
    assert findings == ()
    assert dropped == 1


def test_trimmed_quote_still_grounds() -> None:
    # 真机冒烟教训：逐次采样模型会掐头去尾/改一个标点，全或无匹配把真发现
    # 整条丢掉（dropped=1、findings=0，检测器归零）。长公共片段判据必须容忍。
    raw = (
        '{"contradictions": [{"kind": "timeline", "quote_a": "灶火只剩一个时辰的命！",'
        ' "quote_b": "醉仙楼限他一个月连铺带人吞干净", "why": "期限互斥"}]}'
    )  # quote_a 尾标点被改，quote_b 头部多了主语——核心片段都真实存在
    findings, dropped = parse_and_verify(raw, source_texts=(_BAD_BLURB,))
    assert len(findings) == 1
    assert dropped == 0


def test_paraphrased_quote_is_still_dropped() -> None:
    # 松到长片段为止：整句改写（无 ≥60% 连续原文）仍按幻觉丢弃。
    raw = (
        '{"contradictions": [{"kind": "timeline", "quote_a": "炉子马上就要熄灭了",'
        ' "quote_b": "酒楼给了他三十天期限", "why": "转述而非引用"}]}'
    )
    findings, dropped = parse_and_verify(raw, source_texts=(_BAD_BLURB,))
    assert findings == ()
    assert dropped == 1


def test_quote_match_tolerates_whitespace_only() -> None:
    # 模型常丢换行/空格——压空白后匹配，但一个字都不许改。
    raw = (
        '{"contradictions": [{"kind": "fact", "quote_a": "被骂跑的小徒弟 怀里揣着一筐发光的土豆",'
        ' "quote_b": "他把那筐土豆下了锅", "why": "土豆已被徒弟带走"}]}'
    )
    findings, _ = parse_and_verify(raw, source_texts=(_BAD_BLURB,))
    assert len(findings) == 1


def test_cross_text_contradiction_verifies_against_all_sources() -> None:
    # premise 说三十五岁、spine 说三十年——跨文本矛盾要能核对。
    premise = "三十五岁老城区盒饭铺老板纪釜，开铺七年。"
    spine_line = "who：三十年市井厨房摸爬滚打"
    raw = (
        '{"contradictions": [{"kind": "number", "quote_a": "三十五岁老城区盒饭铺老板",'
        ' "quote_b": "三十年市井厨房摸爬滚打", "why": "35岁减30年=5岁开始颠勺"}]}'
    )
    findings, _ = parse_and_verify(raw, source_texts=(premise, spine_line))
    assert len(findings) == 1
    assert findings[0].kind == "number"


def test_empty_and_garbage_outputs_are_safe() -> None:
    assert parse_and_verify("", source_texts=(_BAD_BLURB,)) == ((), 0)
    assert parse_and_verify("模型话痨不输出JSON", source_texts=(_BAD_BLURB,)) == ((), 0)
    assert parse_and_verify('{"contradictions": "not-a-list"}', source_texts=(_BAD_BLURB,)) == ((), 0)


def test_identical_quotes_rejected() -> None:
    # quote_a == quote_b 不构成矛盾（模型偷懒的常见形态）。
    raw = (
        '{"contradictions": [{"kind": "fact", "quote_a": "房租月底断缴",'
        ' "quote_b": "房租月底断缴", "why": "?"}]}'
    )
    findings, dropped = parse_and_verify(raw, source_texts=(_BAD_BLURB,))
    assert findings == ()
    assert dropped == 1


def test_report_failopen_and_feedback_lines() -> None:
    ok = CoherenceReport(findings=(), llm_used=False)
    assert ok.passed is True  # 判官不可用绝不误毙
    bad = CoherenceReport(
        findings=(
            CoherenceFinding(
                kind="timeline", quote_a="灶火只剩一个时辰的命",
                quote_b="限他一个月连铺带人吞干净", explanation="期限互斥",
            ),
        ),
        llm_used=True,
    )
    assert bad.passed is False
    lines = bad.feedback_lines()
    assert len(lines) == 1
    assert "一个时辰" in lines[0] and "一个月" in lines[0]


def test_prompt_is_a_verification_task_not_a_rating() -> None:
    system, user = build_coherence_messages(
        synopsis=_BAD_BLURB, premise="三十五岁老板", spine={"who": "三十年厨房"},
    )
    # 必须要求逐字引文（可证伪），且明说没有矛盾就给空——不许硬凑。
    assert "逐字" in system
    assert "空列表" in system
    assert "不评价文笔" in system
    assert "【前提】" in user and "【故事脊柱】" in user and "【简介】" in user


# ── ③ 淘汰赛接线 ──────────────────────────────────────────────────────


def test_candidate_with_verified_contradiction_is_ineligible() -> None:
    from bestseller.services.blurb_copywriter import BlurbCandidate

    dirty = BlurbCandidate(
        strategy="scene_hook", synopsis=_BAD_BLURB, gate_score=80.0,
        coherence_contradictions=({"kind": "timeline"},),
    )
    clean = BlurbCandidate(strategy="identity_contrast", synopsis="好稿", gate_score=60.0)
    assert dirty.has_verified_contradiction is True
    assert clean.has_verified_contradiction is False
    # 复刻 survivors 语义：矛盾候选出局，哪怕分更高；全员矛盾时宁可空着回退 v0。
    pool = [c for c in (dirty, clean) if not c.has_fatal_pathology and not c.has_verified_contradiction]
    assert pool == [clean]
    pool_all_dirty = [c for c in (dirty,) if not c.has_verified_contradiction]
    assert pool_all_dirty == []
