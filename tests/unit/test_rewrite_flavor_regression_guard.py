"""rewrite 候选验收的 AI 味回归复检（2026-08-08，第十本终验实锤）。

ai_flavor 闸门只在首稿后跑一次；此后 rewrite 循环修长度时注入
「不是X而是Y」和破折号，候选验收不复检 → 238 次 rewrite 后 10/50 章以
≥38 分出货（ch15=96、ch28=92），破折号中位 5.33→6.13 越修越糟。

判据是【回归】不是绝对分：候选 ≥ 修复线 且 比原稿脏 8 分以上才拒。
本文件用源码结构+真机数据钉语义（验收函数太大，行为级测试走整链集成）。
"""

from __future__ import annotations

import inspect

from bestseller.services import reviews


def _guard_src() -> str:
    src = inspect.getsource(reviews)
    start = src.index("AI 味回归复检")
    return src[start : start + 3000]


def test_guard_exists_at_candidate_acceptance() -> None:
    g = _guard_src()
    # 用候选文本变量 content_md（不是不存在的名字——第一版就栽在 NameError
    # 被 fail-open 吞掉、静默空转）。
    assert "_af_detect(content_md" in g
    assert 'quality_gate_outcome != "blocked"' in g


def test_guard_is_regression_not_absolute() -> None:
    g = _guard_src()
    # 两个条件都必须在：过修复线 AND 比原稿更脏（差 8 分容差）。
    assert "_cand_score >= _af_block" in g
    assert "_cand_score > _orig_score + 8.0" in g


def test_guard_emits_typed_violation_and_blocks() -> None:
    g = _guard_src()
    assert '"AI_FLAVOR_REGRESSION"' in g
    assert 'quality_gate_outcome = "blocked"' in g


def test_real_regression_scores_separate() -> None:
    """真机 ch15 病灶形态（negated_definition 轰炸）必须被判为跨线回归。"""

    from bestseller.services.ai_flavor.detector import detect

    clean = (
        "齐渡把木片贴在碑面上，一笔一画临那行活字。老吏的灯笼在山脚晃了两晃，"
        "停在第三级石阶。他听见自己的心跳，比刻刀落石还响。"
    )
    # 10 处「不是X，而是Y」——真机 ch15 是 15 处（96 分）；warn=4 分/处且
    # negated_definition 刻意不进结构封顶族，10 处 = 40 分即过修复线。
    dirty = (
        "齐渡明白了，这不是刻碑，而是刻命。他要的不是手艺，而是真相。"
        "这一夜不是结束，而是开始。老吏怕的不是碑，而是碑后的人。"
        "他守的不是规矩，而是自己的旧罪。这把刻刀不是工具，而是钥匙。"
        "山脚的灯不是灯，而是眼睛。师父留下的不是遗言，而是考题。"
        "他临的不是字，而是一段人生。碑上的裂纹不是伤，而是门。"
    )
    c = detect(clean, language="zh").overall_score
    d = detect(dirty, language="zh").overall_score
    assert d >= 38.0, d          # 病灶形态必须过修复线
    assert d > c + 8.0, (c, d)   # 且构成回归
