"""提升判据不能建在回声合成分上，也不能建在拒绝真书的判官上。

2026-08-24 离线复验（书 7 恢复到 scratch 库，169 份质量分）三组真机分布：

  * `score_overall`（回声污染的合成分）：均 0.541、**最高 0.62**，
    对着 min_overall=0.85 → **0/169 可达**；拖死它的正是回声公式轴
    （hook 0.278 / contract_alignment 0.284）。
  * 核心最弱维（goal/coverage/coherence/style 的 min）：均 0.773，
    **141/169（83%）≥0.75** → 可达。
  * 16 维商业判官 `pass`：149 份 **0 通过**；把它拿去跑 **10 本真实出版
    小说的章节，10/10 全判 fail**（分数 0.42–0.72，与我们自己的
    0.538 均值完全重叠）→ **在它的通过线上零区分力**。

我 2026-08-23 上午把提升资格改成认这个判官（66264a7），理由是「真尺子掌权」。
数据推翻了那个前提：它是优秀的**批评者**（意见带引文，已接进重写反馈），
但不是合格的**验收尺**。同一天我让回声轴不能否决 verdict，却没把它从提升
判据里摘出来——半个修复。

改：提升看**诚实轴**（core）+ 硬门 + 无阻断码 + 分数指向在架稿；
判官保留教学权（rewrite_plan 回灌）但不再握否决权。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
from uuid import uuid4

from bestseller.domain.promotion import PromotionEvidence, is_promotion_eligible


def _evidence(**over: object) -> PromotionEvidence:
    did = over.pop("draft_id", None) or uuid4()
    base = {
        "draft_id": did,
        "score_draft_id": did,
        "score_overall": 0.56,          # 真机均值量级：回声合成分
        "core_overall": 0.87,           # 诚实轴：goal .92 / coverage .90 / style .82
        "core_scores": (0.92, 0.90, 0.82, 0.82),
        "hard_gates_passed": True,
        "blocking_codes": (),
    }
    base.update(over)
    return PromotionEvidence(**base)  # type: ignore[arg-type]


class TestHonestAxes:
    def test_real_machine_shape_is_now_eligible(self) -> None:
        """真机最常见形状：合成分 0.56（回声拖累）但诚实轴都在 0.8 以上。"""

        assert is_promotion_eligible(_evidence(), min_overall=0.85, min_core=0.75) is True

    def test_weak_core_still_blocks(self) -> None:
        assert (
            is_promotion_eligible(
                _evidence(core_overall=0.62, core_scores=(0.92, 0.90, 0.55, 0.82)),
                min_overall=0.85,
                min_core=0.75,
            )
            is False
        )

    def test_hard_gate_failure_still_blocks(self) -> None:
        assert (
            is_promotion_eligible(
                _evidence(hard_gates_passed=False), min_overall=0.85, min_core=0.75
            )
            is False
        )

    def test_blocking_codes_still_block(self) -> None:
        assert (
            is_promotion_eligible(
                _evidence(blocking_codes=("LENGTH_OVER",)), min_overall=0.85, min_core=0.75
            )
            is False
        )

    def test_score_pointing_at_another_draft_still_blocks(self) -> None:
        assert (
            is_promotion_eligible(
                _evidence(score_draft_id=uuid4()), min_overall=0.85, min_core=0.75
            )
            is False
        )

    def test_missing_core_overall_falls_back_to_composite(self) -> None:
        """老数据没有 core_overall 时退回旧口径，不静默放行。"""

        assert (
            is_promotion_eligible(
                _evidence(core_overall=None), min_overall=0.85, min_core=0.75
            )
            is False
        )


class TestJudgeLosesVeto:
    def test_eligibility_no_longer_consults_the_judge_verdict(self) -> None:
        import inspect

        from bestseller.services import draft_promotion

        src = inspect.getsource(draft_promotion._eligible_row)
        assert "_judge_verdict is not True" not in src, (
            "判官 10/10 拒绝真实出版章节，不能再握提升否决权"
        )
