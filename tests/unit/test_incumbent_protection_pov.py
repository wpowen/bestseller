"""在架稿不该凭 audit_only 缺陷白拿「我是干净的」保护。

2026-08-31 真机《攥着残页从渡口骂到寨里》定罪链：

    POVLockCheck 判 block（第1章叙述层 13 句用错人称）
      → config/quality_gates.yaml 里 POV_DRIFT: audit_only
      → production_state 算出 "ok"（该轴不计入阻断）
      → challenger_takes_current 的「在架稿自己是干净的」保护生效
      → 干净的挑战者被拒，13 条 POV 错误的在架稿被保住

三章实录：第1章 7 稿中 5 稿 POV 干净、第13章 6 稿中 5 稿干净、第14章最新稿
0 条不匹配——**重写反复修好，上架端反复丢弃**。时序重放：13→0、8→0、10→2。

2026-09-01 自我更正：首版只量 POV **一条轴**，而 l6_gate 的 default 就是
audit_only、显式 audit_only 另有 22 条（NAMING_OUT_OF_POOL /
CLIFFHANGER_REPEAT / OPENING_ENTITY_OVERLOAD / FRONT10_* / GOLDEN_THREE_WEAK …）
全在同一个洞里。只捞一条＝「逐轴补丁」的第五次复发。现改为整套码集合比较：
每条轴自动同权，新增检测器自动纳入，无需再逐条接线。

判据要求**单向更差**：在架稿独有的轴 ≥1 且挑战者独有为空。双方各有各的脏时
不动——那种情况谁更差无从判定，保持旧行为比赌一把安全。
"""

import pytest

from bestseller.services.reviews import challenger_takes_current

pytestmark = pytest.mark.unit

BASE = dict(
    challenger_blocked=True,
    incumbent_gate_outcome="ok",   # audit_only 缺陷不会让它变 "blocked"
    has_duplicate_findings=False,
    deterministic_audit_failed=False,
)


def _decide(incumbent_codes, challenger_codes, **overrides):
    """复刻生产判据的算法：单向更差才撤销保护。"""
    worse = set(incumbent_codes) - set(challenger_codes)
    better = set(challenger_codes) - set(incumbent_codes)
    kwargs = dict(BASE, **overrides)
    return challenger_takes_current(
        **kwargs, incumbent_structurally_worse=bool(worse and not better)
    )


class TestUnearnedProtectionIsRevoked:
    def test_the_real_three_chapter_shape_now_swaps(self):
        """第1/13/14 章的真机形态：在架脏 POV、挑战者干净。"""
        assert _decide(["POV_DRIFT"], []) is True

    def test_multiple_dirty_axes_also_swap(self):
        assert _decide(["POV_DRIFT", "CLIFFHANGER_REPEAT"], []) is True

    def test_it_is_not_pov_specific(self):
        """通用化的意义：非 POV 轴同样生效，否则又是逐轴补丁。"""
        assert _decide(["NAMING_OUT_OF_POOL"], []) is True

    def test_a_future_detector_is_covered_without_new_wiring(self):
        """新检测器只要落进 audit_only 就自动纳入，不必再改这里。"""
        assert _decide(["SOME_FUTURE_CHECK"], []) is True

    def test_default_keeps_the_original_protection(self):
        """不传新参数时行为必须与修复前完全一致。"""
        assert challenger_takes_current(**BASE) is False


class TestNoFalseTakeovers:
    def test_a_cleaner_incumbent_is_still_protected(self):
        assert _decide([], ["POV_DRIFT"]) is False

    def test_mutually_dirty_drafts_do_not_swap(self):
        """各有各的脏＝谁更差无从判定，保持旧行为。"""
        assert _decide(["POV_DRIFT"], ["NAMING_OUT_OF_POOL"]) is False

    def test_identical_findings_do_not_swap(self):
        assert _decide(["POV_DRIFT"], ["POV_DRIFT"]) is False

    def test_worse_on_one_axis_but_better_on_another_does_not_swap(self):
        assert _decide(["POV_DRIFT", "HYPE_MISSING"], ["POV_DRIFT", "LINE_GAP_WARN"]) is False


class TestHardUnusableStillWins:
    def test_duplicate_content_beats_the_new_exception(self):
        """重复内容是绝对不可用，优先级不能被新判据翻转。"""
        assert _decide(["POV_DRIFT"], [], has_duplicate_findings=True) is False

    def test_an_unblocked_challenger_always_takes_over(self):
        assert _decide([], [], challenger_blocked=False) is True


class TestWiredIntoTheDecision:
    def test_the_gate_can_report_audit_only_codes(self):
        """相对比较的前提：闸门得说得出「查到了但不阻断」的是哪些码。"""
        import inspect

        from bestseller.services.drafts import _evaluate_chapter_quality_gate

        assert "audit_only_codes_out" in inspect.signature(
            _evaluate_chapter_quality_gate
        ).parameters

    def test_the_rewrite_path_compares_both_drafts(self):
        import inspect

        from bestseller.services import reviews

        src = inspect.getsource(reviews)
        assert "_incumbent_audit_codes - _challenger_audit_codes" in src
        assert "_worse and not _better" in src
        assert "incumbent_structurally_worse=_incumbent_structurally_worse" in src

    def test_the_receipt_carries_the_evidence_not_just_the_verdict(self):
        """「更差」是结论，「差在哪几条轴」才是证据（2026-08-24 回执契约）。"""
        import inspect

        from bestseller.services import reviews

        assert "takeover_incumbent_worse_axes" in inspect.getsource(reviews)

    def test_the_pov_only_helper_is_gone(self):
        """通用化后 POV 专用帮手零消费方——死代码必须清掉，不留误导。"""
        from bestseller.services import reviews

        assert not hasattr(reviews, "_pov_mismatch_count")
        assert not hasattr(reviews, "_POV_TAKEOVER_MARGIN")
