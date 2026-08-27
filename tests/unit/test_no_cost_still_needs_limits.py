"""「无代价≠无限制」——规则写了八天，从来没有实现（2026-08-27）。

用户复述过这条原则：纯爽档去掉的是**代价**（主角自身折损），不是**限制**；
限制是剧情发动机，没有限制就没有博弈，故事跑不起来。

框架**生成端**确实实现了：``cost_style`` ∈ {minimal, external} 时，概念 prompt
注入「无代价≠无限制」+ 限制四型（范围/条件/资格/对象），并要求候选写出
``constraint_ladder``，还写明「若所有场景都在同一层次打转，**项目不成立**」。

**验收端一行都没有**：

  * ``ConceptCandidate`` 从来没有 ``constraint_ladder`` 字段
    → 解析层整块丢弃（真机 custom-xuanhuan-1787757487：12/12 候选零携带，
      冠军没有，项目 metadata 里也没有）
  * 全仓引用 4 处，全部在 concept_tournament 内部（1 处算阶数、1 处 prompt
    schema），**零消费方**
  * prompt 宣称的「项目不成立」没有任何代码去判

于是「限制」这一维度从概念层到成书从未被审过一次，用户看到的
「舔一口就升级、没有任何限制、故事运行不起来」正是这条的产物。

同批审计还发现：``opponent_system`` / ``emotional_promise`` / ``progress_bar``
/ ``endgame_direction`` 等字段**都有下游消费方，但没有任何一条判官轴在给它们
打分**——九条轴全部只评「这一句话钩不钩人」。那是另一条待修，不在本用例范围。

本修复只做**留痕**，不发杀权（本仓库对新检测器的规矩）。
"""

from __future__ import annotations

import pytest

from bestseller.services.concept_tournament import (
    ConceptCandidate,
    audit_constraint_ladder,
    constraint_ladder_tier_target,
)

pytestmark = pytest.mark.unit


def _cand(ladder: list[str]) -> ConceptCandidate:
    return ConceptCandidate(dimension="x", constraint_ladder=tuple(ladder))


def _audit(ladder: list[str], cost_style: str = "minimal", chapters: int = 50):
    return audit_constraint_ladder(
        _cand(ladder), chapter_count=chapters, cost_style=cost_style
    )


class TestTheFieldSurvivesParsingNow:
    def test_the_dataclass_carries_it(self):
        """真机 12/12 候选零携带的直接原因：数据类没有这个字段。"""
        assert _cand(["a", "b"]).constraint_ladder == ("a", "b")

    def test_it_round_trips_through_to_dict(self):
        payload = _cand(["第一阶：外门", "第二阶：内门"]).to_dict()
        assert payload["constraint_ladder"] == ["第一阶：外门", "第二阶：内门"]

    def test_vacuity_a_candidate_without_the_field_defaults_empty(self):
        """空转检验：字段缺失时必须是空元组，不能变成 None 或报错。"""
        assert ConceptCandidate(dimension="x").constraint_ladder == ()


class TestTheAuditCatchesTheRealBook:
    def test_the_real_shape_is_flagged(self):
        """真机：纯爽档 + 空阶梯。"""
        r = _audit([])
        assert r["required"] is True
        assert r["passed"] is False
        assert "constraint_ladder_missing" in r["findings"]

    def test_a_short_ladder_is_flagged(self):
        r = _audit(["第一阶：外门饭堂", "第二阶：内门丹房"])
        assert any(f.startswith("constraint_ladder_short") for f in r["findings"])

    def test_tiers_stuck_in_one_layer_are_flagged(self):
        """prompt 说的「同一层次打转」——可判形态是互相包含或逐字重复。"""
        assert "constraint_ladder_flat" in _audit(["只在外门", "只在外门饭堂"])["findings"]
        assert "constraint_ladder_flat" in _audit(["只在外门", "只在外门"])["findings"]

    def test_a_real_ladder_passes(self):
        r = _audit(
            [
                "第一阶：只在外门饭堂，尝掺假换跑腿抽成",
                "第二阶：解锁内门丹房，替长老验丹材",
                "第三阶：解锁郡城商会，判整条商路的真假",
                "第四阶：解锁宗门大比，当众定夺一批贡品",
            ]
        )
        assert r["passed"] is True
        assert r["tiers"] == 4


class TestScopeAndSemantics:
    def test_standard_cost_style_is_not_required_to_have_one(self):
        """standard 档代价本身就是发动机，不强求限制阶梯。"""
        r = _audit([], cost_style="standard")
        assert r["required"] is False
        assert r["passed"] is True

    def test_external_cost_style_is_required_too(self):
        assert _audit([], cost_style="external")["required"] is True

    def test_the_tier_target_scales_with_chapter_count(self):
        assert constraint_ladder_tier_target(12) <= constraint_ladder_tier_target(200)

    def test_the_receipt_is_always_written(self):
        """恒非空——没有回执就分不清「查了没问题」和「压根没查」，
        这正是本案被埋了八天的原因。"""
        for style in ("minimal", "external", "standard"):
            r = _audit([], cost_style=style)
            assert set(r) >= {"required", "cost_style", "tiers", "tier_target", "findings", "passed"}


class TestItOnlyLeavesATrace:
    def test_the_audit_does_not_reject_the_winner(self):
        """新检测器只挣留痕，不发杀权——冠军不因这条被换掉。"""
        import inspect

        from bestseller.services import concept_tournament

        src = inspect.getsource(concept_tournament.audit_constraint_ladder)
        assert "rejected_reason" not in src
        assert "raise" not in src

    def test_the_result_object_exposes_the_receipt(self):
        import inspect

        from bestseller.services import concept_tournament

        src = inspect.getsource(concept_tournament)
        assert "constraint_ladder_audit: dict[str, Any]" in src
        assert '"constraint_ladder_audit": dict(self.constraint_ladder_audit)' in src
