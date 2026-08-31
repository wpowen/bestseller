"""在架稿的「我是干净的」保护不该被 audit_only 缺陷白拿。

2026-08-31 真机《攥着残页从渡口骂到寨里》定罪链：

    POVLockCheck 判 block（第1章叙述层 13 句用错人称）
      → config/quality_gates.yaml 里 POV_DRIFT: audit_only
      → production_state 算出 "ok"（POV 不计入阻断）
      → challenger_takes_current 的「在架稿自己是干净的」保护生效
      → 干净的挑战者被拒，13 条 POV 错误的在架稿被保住

三章实录：第1章 7 稿中 5 稿 POV 干净、第13章 6 稿中 5 稿干净、第14章最新稿
0 条不匹配——**重写反复修好，上架端反复丢弃**。逐份时序重放证明修复有效：
第1章 13→0、第13章 8→0、第14章 10→2。

修法只撤销不该有的保护，**不给 POV_DRIFT 任何杀权**（audit_only 保持原样）。
"""

import pytest

from bestseller.services.reviews import (
    _POV_TAKEOVER_MARGIN,
    _pov_mismatch_count,
    challenger_takes_current,
)

pytestmark = pytest.mark.unit

BASE = dict(
    challenger_blocked=True,
    incumbent_gate_outcome="ok",   # audit_only 缺陷不会让它变 "blocked"
    has_duplicate_findings=False,
    deterministic_audit_failed=False,
)


class _Project:
    language = "zh-CN"


def _prose(first_person_sentences: int, other: int = 30) -> str:
    a = "。".join("我攥着那截残页往寨门走" for _ in range(first_person_sentences))
    b = "。".join("沈鹊攥着那截残页往寨门走" for _ in range(other))
    return (a + "。" if a else "") + b + "。"


class TestUnearnedProtectionIsRevoked:
    def test_a_pov_dirty_incumbent_no_longer_blocks_a_clean_challenger(self):
        assert challenger_takes_current(**BASE, incumbent_structurally_worse=True) is True

    def test_default_keeps_the_original_protection(self):
        """不传新参数时行为必须与修复前完全一致。"""
        assert challenger_takes_current(**BASE) is False


class TestNoFalseTakeovers:
    def test_a_cleaner_incumbent_is_still_protected(self):
        assert challenger_takes_current(**BASE, incumbent_structurally_worse=False) is False

    def test_hard_unusable_challenger_still_loses_even_if_incumbent_is_worse(self):
        """重复内容是绝对不可用，与在架稿好坏无关——优先级不能被新判据翻转。"""
        kw = dict(BASE, has_duplicate_findings=True)
        assert challenger_takes_current(**kw, incumbent_structurally_worse=True) is False

    def test_an_unblocked_challenger_always_takes_over(self):
        kw = dict(BASE, challenger_blocked=False)
        assert challenger_takes_current(**kw, incumbent_structurally_worse=False) is True


class TestMismatchCounter:
    def test_it_counts_first_person_narration(self):
        assert _pov_mismatch_count(_prose(8), _Project()) >= 6

    def test_clean_third_person_prose_scores_zero(self):
        assert _pov_mismatch_count(_prose(0), _Project()) == 0

    def test_empty_text_is_safe(self):
        assert _pov_mismatch_count("", _Project()) == 0

    def test_dialogue_first_person_is_not_counted(self):
        """引号内的「我」是对白，不是叙述层人称——必须先被剥掉。"""
        text = "。".join('沈鹊说：「我不走」' for _ in range(20)) + "。"
        assert _pov_mismatch_count(text, _Project()) == 0


class TestMarginIsNotZero:
    def test_noise_level_differences_do_not_trigger_a_swap(self):
        """边距存在的意义：自由间接引语噪声不该引发换稿抖动。"""
        assert _POV_TAKEOVER_MARGIN >= 3


class TestWiredIntoTheDecision:
    def test_the_rewrite_path_computes_and_passes_the_comparison(self):
        import inspect

        from bestseller.services import reviews

        src = inspect.getsource(reviews)
        assert "_incumbent_structurally_worse = _pov_mismatch_count(" in src
        assert "incumbent_structurally_worse=_incumbent_structurally_worse" in src
