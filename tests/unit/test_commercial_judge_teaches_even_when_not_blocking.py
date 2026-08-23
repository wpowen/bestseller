"""判官的重写方案必须回灌，哪怕它没有阻断权。

2026-08-23 离线复验（书 7 恢复到 scratch 库，169 份质量分）：16 维商业判官
149 份判决**全部 fail**，均分 0.538、最高 0.78、零份到 0.80。它的意见质量很高
——逐条带引文，还给出 `rewrite_plan.instructions`（「十个程序术语压到四件以内」
「爽点要有反派立得住的反应」）。

但接线是这样的：`rewrite_plan.instructions` 只有在
`chapter_llm_commercial_judge_block_on_failure=True` 时才会变成
`rewrite_instructions`；该开关默认 **False**，于是走 else 分支，方案被原样丢弃
——判官只把 payload 存进证据里。

而今早的提升修复（66264a7）让这个判官掌握了**提升否决权**。两件事合起来
就是「能毙但不教」：它否决章节，却从不把怎么改告诉写手，写手按另一套
（关键词/critic）指令重写，永远收敛不到判官要的方向。

修：把「是否阻断」与「是否教学」拆开。判官未通过时，它的重写方案一律并入
重写指令；`block_on_failure` 只继续决定要不要强制把 verdict 改成 rewrite。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002 — 中文标点是刻意的。
from bestseller.services.reviews import merge_judge_rewrite_direction


class TestMergeDirection:
    def test_failed_judge_plan_is_appended_to_existing_instructions(self) -> None:
        merged = merge_judge_rewrite_direction(
            "原有指令：补齐场景锚点。",
            judge_payload={
                "pass": False,
                "rewrite_plan": {"instructions": "十个程序术语压到四件以内。"},
                "blocking_issues": [],
            },
        )
        assert "原有指令：补齐场景锚点。" in merged
        assert "十个程序术语压到四件以内。" in merged

    def test_blocking_issues_are_used_when_plan_has_no_instructions(self) -> None:
        merged = merge_judge_rewrite_direction(
            "",
            judge_payload={
                "pass": False,
                "rewrite_plan": {},
                "blocking_issues": [
                    {"code": "PAYOFF_DENSITY_LOW", "required_fix": "补一段反派反应。"}
                ],
            },
        )
        assert "PAYOFF_DENSITY_LOW" in merged
        assert "补一段反派反应。" in merged

    def test_passing_judge_adds_nothing(self) -> None:
        base = "原有指令。"
        assert (
            merge_judge_rewrite_direction(
                base,
                judge_payload={"pass": True, "rewrite_plan": {"instructions": "无关"}},
            )
            == base
        )

    def test_missing_or_malformed_payload_is_a_noop(self) -> None:
        assert merge_judge_rewrite_direction("原有", judge_payload=None) == "原有"
        assert merge_judge_rewrite_direction("原有", judge_payload={}) == "原有"
        assert merge_judge_rewrite_direction("原有", judge_payload=[1, 2]) == "原有"

    def test_no_duplicate_when_plan_already_present(self) -> None:
        plan = "十个程序术语压到四件以内。"
        merged = merge_judge_rewrite_direction(
            plan,
            judge_payload={"pass": False, "rewrite_plan": {"instructions": plan}},
        )
        assert merged.count(plan) == 1


class TestWiring:
    def test_non_blocking_branch_merges_the_plan(self) -> None:
        """接线钉：block_on_failure=False 的那条分支也要带上判官方案。"""

        import inspect

        from bestseller.services import reviews

        src = inspect.getsource(reviews.review_chapter_draft)
        # else 分支里构造 ChapterReviewResult 时必须调用合并函数
        assert "merge_judge_rewrite_direction(" in src
