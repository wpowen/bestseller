"""概念层的两个测量缺口（2026-08-25 真机 custom-xuanhuan-1787625194）。

用户报「这本书完全没有任何可读性」。框架自己的整书质量账本同意：
7–11 章里 **has_decision 0/5**、3/5 章 ``functional_shape=flat``、``passed=false``。
但这些全是在 12 章写完**之后**才被拦下的——先写再拦。

往上游查，概念层有两个缺口：

E. 判官八条轴里**没有一条问「主角要不要做选择」**。批准的故事引擎是纯被动
   循环（外部压力每轮施加、主角每轮应对），正文 has_decision 0/5 是它的必然
   产物，不是写手写砸了。

D. 唯一评追读性的判官（renewability / escalation / anti_reset / coherence /
   promise_survival / unit_density）整段挂在写死的 ``chapter_count >= 200``
   后面，对所有正常长度的书完全空转；``seriality_judge`` 恒为 ``{}``，
   「评了没发现」与「压根没跑」不可区分。

两条的修法都刻意**不发杀权**：E 只进 composite 加权（改排序不改淘汰），
D 在短书上只留痕不否决。理由是 config 里 2026-07-17 的教训——收紧概念层
阈值会让淘汰赛干涸，而干涸的下场是注入保底概念，比任何被拒候选都差。
"""

from __future__ import annotations

import pytest

from bestseller.services.concept_tournament import (
    _FLOOR_AXIS_LABELS,
    _hard_floor_failed_axes,
    seriality_stage_mode,
)

pytestmark = pytest.mark.unit


class TestProtagonistAgencyAxis:
    def test_the_axis_is_asked_for_in_the_judge_prompt(self):
        """轴必须真的问出去——只加解析不加 prompt 等于恒拿默认分。"""
        from bestseller.services import concept_tournament as ct

        src = ct.__file__
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        assert "protagonist_agency 主角能动性" in text
        assert '"protagonist_agency": 0-10' in text

    def test_the_axis_has_no_hard_floor(self):
        """刻意不发杀权：只改排序不改淘汰，避免重演 2026-07-17 的干涸事故。"""
        floored = {key for key, _label, _default in _FLOOR_AXIS_LABELS}
        assert "protagonist_agency" not in floored

    def test_a_passive_engine_is_not_eliminated_by_the_new_axis_alone(self):
        """能动性极低但其余达标的候选仍进池——降权不是杀权。"""
        scores = {
            "freshness": 7.0,
            "click": 7.5,
            "predictable": 5.0,
            "character_logic": 7.5,
            "mechanism_causality": 7.5,
            "genre_fidelity": 7.5,
            "plain_language": 7.5,
            "story_motion": 7.5,
            "protagonist_agency": 1.0,
        }
        floors = {"catastrophe_floor": 4.0, "soft_miss_allowance": 3}
        assert _hard_floor_failed_axes(scores, floors) == []

    @pytest.mark.parametrize("marker,label", [
        ("\njudge_weights:", "基线"),
        ("\n  judge_weights:", "wild_mode"),
    ])
    def test_every_weight_set_sums_to_one(self, marker: str, label: str):
        """**每一套**权重都要配平，不只是基线。

        2026-08-25 实测：新轴在代码里给了非零默认（0.10），于是它被加到
        wild_mode 这套没声明该键的权重上，合计从 1.0 变 1.1，composite 与
        winner_min 的关系被悄悄改变——全量套件当场抓到
        （test_penalize_keeps_cliche_candidate_alive 期望 7.5 实得 8.0）。
        本用例是那条失败的结构化版本：加轴时忘了同步某一套，这里就红。
        """
        import re
        from pathlib import Path

        text = Path("config/concept_tournament.yaml").read_text(encoding="utf-8")
        assert marker in text, f"{label} 权重块不存在"
        block = text.split(marker, 1)[1].split("\n\n", 1)[0]
        values = [float(v) for v in re.findall(r"^ +[a-z_]+: ([0-9.]+)", block, re.M)]
        assert "protagonist_agency" in block, f"{label} 缺 protagonist_agency"
        assert abs(sum(values) - 1.0) < 1e-9, f"{label} 权重合计 {sum(values)}"

    def test_the_code_default_for_a_new_axis_is_zero(self):
        """新轴在代码里必须默认 0.0——真值只许有一个来源（配置）。"""
        import inspect

        from bestseller.services import concept_tournament as ct

        src = inspect.getsource(ct)
        assert 'weights.get("protagonist_agency", 0.0)' in src


class TestSerialityStageTiering:
    def test_long_serials_keep_the_enforcing_behaviour(self):
        """≥200 章维持既有行为，一个字节都不改。"""
        mode, receipt = seriality_stage_mode(200, {})
        assert mode == "enforcing"
        assert receipt["enforcing_min_chapters"] == 200

    def test_a_normal_book_now_reaches_the_judge_in_advisory_mode(self):
        """真机那本 12 章书此前整段跳过；现在跑判官但不发否决。"""
        mode, _ = seriality_stage_mode(12, {})
        assert mode == "advisory"

    def test_a_very_short_piece_is_still_skipped(self):
        """短篇没有「长篇承载」可言，不制造新的失败模式。"""
        assert seriality_stage_mode(3, {})[0] == "skipped"

    def test_thresholds_are_configurable_not_hardcoded(self):
        """写死的魔数不可校准也不可回滚。"""
        cfg = {"seriality_min_chapters": 30, "seriality_advisory_min_chapters": 5}
        assert seriality_stage_mode(30, cfg)[0] == "enforcing"
        assert seriality_stage_mode(29, cfg)[0] == "advisory"
        assert seriality_stage_mode(4, cfg)[0] == "skipped"

    def test_the_receipt_is_always_written(self):
        """三态都要留痕——此前 seriality_judge=={} 分不清没跑还是没发现。"""
        for chapters in (3, 12, 500):
            _mode, receipt = seriality_stage_mode(chapters, {})
            assert receipt["mode"] in {"enforcing", "advisory", "skipped"}
            assert receipt["chapter_count"] == chapters

    def test_vacuity_the_old_hardcoded_gate_would_fail_this_suite(self):
        """空转检验：还原写死的 200 判定，确认本套件抓得住它。"""

        def old_gate(chapter_count: int) -> str:
            return "enforcing" if chapter_count >= 200 else "skipped"

        assert old_gate(12) == "skipped"
        assert seriality_stage_mode(12, {})[0] == "advisory", (
            "修复前 12 章书整段跳过，本套件第二条断言正是为它写的"
        )
