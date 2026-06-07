"""Tests for the scene-level LLM-pass-over-rule-rewrite semantic authority.

The deterministic scene scorer is keyword-echo; `overall` falls below threshold
even for genuinely good prose. When LLM scene commentary explicitly passes and
only advisory craft-axis findings remain (no structural defect), the rule-based
"rewrite" must yield to the LLM's "pass". Structural findings (duplication, wrong
character name, output hygiene) or any critical finding must still block.
"""

from __future__ import annotations

import pytest

from bestseller.domain.review import (
    SceneReviewFinding,
    SceneReviewResult,
    SceneReviewScores,
)
from bestseller.services.reviews import (
    _can_accept_scene_llm_pass_over_rule_rewrite,
)


def _scores() -> SceneReviewScores:
    # Low-ish overall (keyword-echo) — the exact case the override is meant for.
    return SceneReviewScores(
        overall=0.53,
        goal=1.0,
        conflict=0.55,
        conflict_clarity=0.25,
        emotion=0.4,
        emotional_movement=0.22,
        dialogue=0.84,
        style=0.82,
        hook=0.36,
        hook_strength=0.26,
        payoff_density=0.59,
        voice_consistency=0.75,
        character_voice_distinction=1.0,
        thematic_resonance=0.7,
        worldbuilding_integration=0.82,
        prose_variety=0.61,
        moral_complexity=0.55,
        contract_alignment=0.11,
    )


def _result(findings: list[SceneReviewFinding]) -> SceneReviewResult:
    return SceneReviewResult(
        verdict="rewrite",
        severity_max="high",
        scores=_scores(),
        findings=findings,
    )


@pytest.mark.unit
def test_override_accepts_when_only_craft_axis_findings():
    """All findings are advisory craft axes → LLM 'pass' may override."""
    findings = [
        SceneReviewFinding(category="hook_strength", severity="high", message="弱钩子"),
        SceneReviewFinding(category="contract_alignment", severity="high", message="契约对齐低"),
        SceneReviewFinding(category="emotional_movement", severity="high", message="情绪位移弱"),
    ]
    assert _can_accept_scene_llm_pass_over_rule_rewrite(_result(findings)) is True


@pytest.mark.unit
def test_override_blocked_by_duplication():
    """A structural duplication finding must keep blocking despite LLM pass."""
    findings = [
        SceneReviewFinding(category="hook_strength", severity="high", message="弱钩子"),
        SceneReviewFinding(category="duplication", severity="major", message="与前文重复"),
    ]
    assert _can_accept_scene_llm_pass_over_rule_rewrite(_result(findings)) is False


@pytest.mark.unit
def test_override_blocked_by_character_consistency():
    findings = [
        SceneReviewFinding(category="character_consistency", severity="high", message="角色名错误"),
    ]
    assert _can_accept_scene_llm_pass_over_rule_rewrite(_result(findings)) is False


@pytest.mark.unit
def test_override_blocked_by_output_hygiene():
    findings = [
        SceneReviewFinding(category="output_hygiene", severity="high", message="正文混入元数据"),
    ]
    assert _can_accept_scene_llm_pass_over_rule_rewrite(_result(findings)) is False


@pytest.mark.unit
def test_override_blocked_by_critical_severity():
    """Even an advisory-category finding blocks if it is critical."""
    findings = [
        SceneReviewFinding(category="contract_alignment", severity="critical", message="严重缺契约"),
    ]
    assert _can_accept_scene_llm_pass_over_rule_rewrite(_result(findings)) is False


@pytest.mark.unit
def test_override_not_applicable_when_already_pass():
    res = SceneReviewResult(
        verdict="pass",
        severity_max="low",
        scores=_scores(),
        findings=[],
    )
    assert _can_accept_scene_llm_pass_over_rule_rewrite(res) is False
