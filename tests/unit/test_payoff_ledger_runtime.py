from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest import mock

from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores
from bestseller.services.payoff_ledger import run_payoff_ledger_audit
from bestseller.services.payoff_ledger_runtime import (
    merge_payoff_ledger_audit_into_chapter_review,
    payoff_ledger_audit_to_dict,
    render_payoff_ledger_planner_contract,
)


@dataclass(frozen=True)
class FakeClue:
    id: str
    clue_code: str
    planted_in_chapter_number: int | None = None


@dataclass(frozen=True)
class FakePayoff:
    payoff_code: str
    label: str = ""
    description: str = ""
    source_clue_id: Any | None = None
    target_chapter_number: int | None = None
    actual_chapter_number: int | None = None
    status: str = "planned"
    metadata_json: dict[str, Any] = field(default_factory=dict)


def _passing_review() -> ChapterReviewResult:
    scores = ChapterReviewScores(
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
    )
    return ChapterReviewResult(
        verdict="pass",
        severity_max="info",
        scores=scores,
        findings=[],
        evidence_summary={},
    )


def test_payoff_ledger_audit_payload_is_json_safe() -> None:
    audit = run_payoff_ledger_audit(
        [FakePayoff("late", target_chapter_number=4)],
        current_chapter=5,
    )

    payload = payoff_ledger_audit_to_dict(audit)

    assert payload["closure_rate"] == 0.0
    assert payload["overdue_count"] == 1
    assert payload["entries"][0]["status"] == "overdue"
    assert payload["findings"][0]["code"] == "PAYOFF_OVERDUE"


def test_merge_payoff_ledger_audit_promotes_rewrite_for_due_payoff() -> None:
    audit = run_payoff_ledger_audit(
        [FakePayoff("due", target_chapter_number=5)],
        current_chapter=5,
    )

    merged = merge_payoff_ledger_audit_into_chapter_review(
        _passing_review(),
        audit,
        chapter_number=5,
        language="zh-CN",
    )

    assert merged.verdict == "rewrite"
    assert merged.severity_max == "major"
    assert "兑现账本修复" in (merged.rewrite_instructions or "")
    assert "payoff_ledger_audit" in merged.evidence_summary
    assert any(f.category == "payoff_ledger" for f in merged.findings)


def test_merge_payoff_ledger_audit_preserves_pass_when_clean() -> None:
    audit = run_payoff_ledger_audit(
        [FakePayoff("future", target_chapter_number=8)],
        current_chapter=5,
    )

    merged = merge_payoff_ledger_audit_into_chapter_review(
        _passing_review(),
        audit,
        chapter_number=5,
        language="zh-CN",
    )

    assert merged.verdict == "pass"


def test_render_payoff_ledger_planner_contract_defaults_off() -> None:
    with mock.patch.dict("os.environ", {}, clear=True):
        assert render_payoff_ledger_planner_contract(language="zh-CN") == ""


def test_render_payoff_ledger_planner_contract_when_enabled_zh() -> None:
    with mock.patch.dict("os.environ", {"BESTSELLER_METHODOLOGY_V2": "1"}):
        block = render_payoff_ledger_planner_contract(language="zh-CN")

    assert "方法论 v2 兑现账本合同" in block
    assert "payoffs_due" in block
    assert "payoff_evidence_paths" in block
    assert "至少兑现 1 个到期 payoff" in block
    assert "setup distance" in block


def test_render_payoff_ledger_planner_contract_when_enabled_en() -> None:
    with mock.patch.dict("os.environ", {"BESTSELLER_METHODOLOGY_V2": "1"}):
        block = render_payoff_ledger_planner_contract(language="en-US")

    assert "Methodology v2 payoff ledger contract" in block
    assert "payoffs_due" in block
    assert "setup distance" in block
    assert "visible in-prose callback" in block
