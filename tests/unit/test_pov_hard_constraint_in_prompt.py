"""人称写进硬约束块（2026-08-20 真机《罚我守坟》定罪）。

真机 8/21 章整章第一人称，而全书 close_third。查 prompt 发现：POV 只以
「- 视角：third-limited / 时态：present」一行的形式，埋在 PROJECT PROFILE
那坨 ~20 行的画像 blob 里；user prompt 里**一个字都没有**；
system 的「# CONSTRAINTS · 硬约束（违反即重写）」列了场景标签 / 策划泄漏 /
角色名 / 输出格式四条，**唯独没有人称**——而人称写错比场景标签泄漏
严重得多。

这是类别级指令（叙述层用第三人称 + 正例「他/她」），不是词表种词。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import drafts

pytestmark = pytest.mark.unit


def test_pov_is_a_hard_constraint_not_only_a_profile_line():
    src = inspect.getsource(drafts.build_chapter_first_draft_prompts)
    assert "_pov_hard_constraint" in src, "人称必须进硬约束块，不能只躺在画像 blob 里"


def test_third_person_rule_names_the_narration_layer_only():
    block = drafts._pov_hard_constraint("third-limited", language="zh-CN")
    assert block, "third-limited 必须产出约束"
    assert "叙述" in block
    # 对白/内心引号里的「我」是合法的，规则必须说清楚，否则会误伤
    assert "引号" in block or "对白" in block


def test_first_person_book_gets_the_mirror_rule():
    block = drafts._pov_hard_constraint("first", language="zh-CN")
    assert block and "第一人称" in block


def test_unknown_pov_emits_nothing():
    assert drafts._pov_hard_constraint("", language="zh-CN") == ""
    assert drafts._pov_hard_constraint("omniscient", language="zh-CN") == ""


def test_english_branch_gets_it_too():
    """两条语言分支都要接线——只修一条正是今天反复撞的元病。"""
    src = inspect.getsource(drafts.build_chapter_first_draft_prompts)
    assert src.count("_pov_hard_constraint(writing_profile.style.pov_type") == 2
    block = drafts._pov_hard_constraint("close_third", language="en")
    assert "THIRD person" in block
