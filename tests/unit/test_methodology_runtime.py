from __future__ import annotations

import pytest

from bestseller.domain.review import (
    ChapterReviewResult,
    ChapterReviewScores,
    SceneReviewResult,
    SceneReviewScores,
)
from bestseller.services.checker_schema import CheckerIssue, CheckerReport
from bestseller.services.methodology_runtime import (
    METHODOLOGY_REPORTS_EVIDENCE_KEY,
    checker_reports_from_review_payload,
    merge_methodology_reports_into_chapter_review,
    merge_methodology_reports_into_scene_review,
)

pytestmark = pytest.mark.unit


def _issue(
    issue_id: str,
    *,
    severity: str = "high",
    can_override: bool = True,
) -> CheckerIssue:
    return CheckerIssue(
        id=issue_id,
        type="methodology_action_scene",
        severity=severity,  # type: ignore[arg-type]
        location="chapter 1 scene 1",
        description="缺少动作目标",
        suggestion="补出战斗目标和失败代价",
        can_override=can_override,
    )


def _report(issue: CheckerIssue) -> CheckerReport:
    return CheckerReport(
        agent="action-scene-structure-gate",
        chapter=1,
        overall_score=85,
        passed=False,
        issues=(issue,),
    )


def _scene_review() -> SceneReviewResult:
    return SceneReviewResult(
        verdict="pass",
        severity_max="info",
        scores=SceneReviewScores(
            overall=0.9,
            goal=0.9,
            conflict=0.9,
            conflict_clarity=0.9,
            emotion=0.9,
            emotional_movement=0.9,
            dialogue=0.9,
            style=0.9,
            hook=0.9,
            hook_strength=0.9,
            payoff_density=0.9,
            voice_consistency=0.9,
            character_voice_distinction=0.9,
            thematic_resonance=0.9,
            worldbuilding_integration=0.9,
            prose_variety=0.9,
            moral_complexity=0.9,
            contract_alignment=0.9,
        ),
    )


def _chapter_review() -> ChapterReviewResult:
    return ChapterReviewResult(
        verdict="pass",
        severity_max="info",
        scores=ChapterReviewScores(
            overall=0.9,
            goal=0.9,
            coverage=0.9,
            coherence=0.9,
            continuity=0.9,
            main_plot_progression=0.9,
            subplot_progression=0.9,
            style=0.9,
            hook=0.9,
            ending_hook_effectiveness=0.9,
            volume_mission_alignment=0.9,
            pacing_rhythm=0.9,
            character_voice_distinction=0.9,
            thematic_resonance=0.9,
            contract_alignment=0.9,
        ),
    )


def test_audit_only_methodology_scene_issue_surfaces_without_forcing_rewrite() -> None:
    report = _report(_issue("ACTION_SCENE_OBJECTIVE_MISSING", can_override=True))

    result = merge_methodology_reports_into_scene_review(_scene_review(), (report,))

    assert result.verdict == "pass"
    assert result.severity_max == "major"
    assert result.findings[0].category == "methodology_action_scene"
    assert METHODOLOGY_REPORTS_EVIDENCE_KEY in result.evidence_summary


def test_strict_methodology_chapter_issue_forces_rewrite() -> None:
    report = _report(
        _issue("OPENING_CH1_PRESSURE_MISSING", severity="high", can_override=False)
    )

    result = merge_methodology_reports_into_chapter_review(_chapter_review(), (report,))

    assert result.verdict == "rewrite"
    assert result.severity_max == "critical"
    assert "方法论 Gate" in (result.rewrite_instructions or "")


def test_checker_reports_round_trip_from_review_payload() -> None:
    report = _report(_issue("CHEKHOV_USE_OVERDUE"))
    payload = {
        "evidence_summary": {
            METHODOLOGY_REPORTS_EVIDENCE_KEY: [report.to_dict()],
        }
    }

    extracted = checker_reports_from_review_payload(payload)

    assert len(extracted) == 1
    assert extracted[0].issues[0].id == "CHEKHOV_USE_OVERDUE"
