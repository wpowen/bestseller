"""项目级一致性审稿：任何一条发现就判 attention = 一道永不通过的门。

2026-08-23 深度排查（书 7 恢复到 scratch 库，42 章跑完）用户报「书籍创建完成
之后没有完整走整个框架的流程，时灵时不灵」。逐层量下来因果链是：

  能力层几乎不落数据（canon 6 条、人际承诺 0 条、伏笔账本被 prompt 预算淘汰）
  → 项目级一致性审稿发现这些空洞（canon_coverage / foreshadowing_balance /
    timeline_coverage 等 6 条 high，12 次审稿次次相同）
  → `verdict = "pass" if overall >= threshold and **not findings** else "attention"`
  → attention ∈ _ATTENTION_VERDICTS → requires_human_review=True
  → `if not requires_human_review: status = COMPLETED` 不执行，顶层 workflow
    **永不完成**（真机：project_pipeline 6 次运行 0 完成、project_repair 7 次 0 完成）
  → 自愈看到未终结的书就重启 → 再跑一遍还是 attention → 循环 12 次
  → 每次重启停在不同位置，审稿之后的阶段就「时而走到时而走不到」。

`not findings` 要求**一条发现都没有**。42 章的书必然有 low/medium 级发现，
所以这道门在结构上不可能通过——与本仓库定案过的「从不失败的门等于不存在的
门」是同一枚硬币的反面：**从不通过的门等于永远卡住**。

修：判定只看**阻断级（high）**发现；low/medium 照旧记录并进整改建议，但不
再单独把整本书钉死在人工复核上。high 仍然拦——书 7 那 6 条 high 是真的。
"""

from __future__ import annotations

# ruff: noqa: RUF002 — 中文标点是刻意的。
from bestseller.services.consistency import project_verdict_from_findings


class _F:
    def __init__(self, severity: str, category: str = "x") -> None:
        self.severity = severity
        self.category = category


class TestSeverityGating:
    def test_low_and_medium_alone_do_not_block(self) -> None:
        # 真机 12 次审稿里 world_rule_consistency / supporting_cast_depth /
        # emotion_continuity / foreshadowing_density 都是 medium。
        verdict = project_verdict_from_findings(
            overall=0.82,
            threshold=0.75,
            findings=[_F("medium", "world_rule_consistency"), _F("low", "revision_pressure")],
        )
        assert verdict == "pass"

    def test_a_single_high_finding_blocks(self) -> None:
        verdict = project_verdict_from_findings(
            overall=0.95,
            threshold=0.75,
            findings=[_F("high", "canon_coverage")],
        )
        assert verdict == "attention"

    def test_low_overall_still_blocks_even_without_findings(self) -> None:
        verdict = project_verdict_from_findings(overall=0.40, threshold=0.75, findings=[])
        assert verdict == "attention"

    def test_clean_and_above_threshold_passes(self) -> None:
        assert project_verdict_from_findings(overall=0.9, threshold=0.75, findings=[]) == "pass"

    def test_real_machine_book7_shape_still_blocks(self) -> None:
        """书 7 那 6 条 high 是真的——修完这道门它照样该拦。"""

        findings = [
            _F("high", c)
            for c in (
                "chapter_status",
                "character_arc_progression",
                "timeline_coverage",
                "resolution_completeness",
                "canon_coverage",
                "foreshadowing_balance",
            )
        ] + [_F("medium", "world_rule_consistency"), _F("low", "revision_pressure")]
        assert project_verdict_from_findings(overall=0.8, threshold=0.75, findings=findings) == "attention"


class TestWiring:
    def test_evaluator_uses_the_shared_rule(self) -> None:
        """⚠️ 首版指错了函数（review_project_consistency）——判定其实在
        evaluate_project_consistency 里。断言前先确认自己钉的是真正算 verdict
        的那个函数。"""

        import inspect

        from bestseller.services import consistency

        src = inspect.getsource(consistency.evaluate_project_consistency)
        assert "project_verdict_from_findings(" in src
        # 旧的「任何发现即拦」写法必须消失
        assert "and not findings else" not in src
