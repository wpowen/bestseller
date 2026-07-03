from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest import mock

from bestseller.domain.review import (
    ChapterReviewResult,
    ChapterReviewScores,
)
from bestseller.services.hook_ledger import run_hook_ledger_audit
from bestseller.services.hook_ledger_runtime import (
    hook_ledger_audit_to_dict,
    merge_hook_ledger_audit_into_chapter_review,
    render_hook_ledger_planner_contract,
)


@dataclass(frozen=True)
class FakeClue:
    clue_code: str
    clue_type: str = "foreshadow"
    label: str = ""
    planted_in_chapter_number: int | None = None
    expected_payoff_by_chapter_number: int | None = None
    actual_paid_off_chapter_number: int | None = None
    status: str = "planted"
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


def test_render_hook_ledger_planner_contract_defaults_off() -> None:
    with mock.patch.dict("os.environ", {}, clear=True):
        assert render_hook_ledger_planner_contract(language="zh-CN") == ""


def test_render_hook_ledger_planner_contract_when_enabled() -> None:
    with mock.patch.dict("os.environ", {"BESTSELLER_METHODOLOGY_V2": "1"}):
        block = render_hook_ledger_planner_contract(language="zh-CN")

    assert "方法论 v2 钩子台账合同" in block
    assert "hooks_to_resolve" in block
    assert "information_gap" in block
    assert "第 1 章可植入 2-3 个钩子" in block
    assert "消解数不少于植入数" in block


def test_hook_ledger_audit_payload_is_json_safe() -> None:
    audit = run_hook_ledger_audit(
        [FakeClue("c1", clue_type="deadline", planted_in_chapter_number=1)],
        current_chapter=20,
    )

    payload = hook_ledger_audit_to_dict(audit)

    assert payload["closure_rate"] == 0.0
    assert payload["overdue_codes"] == ["c1"]
    assert payload["by_type"]["deadline"] == 0
    assert payload["findings"][0]["code"] == "HOOK_ACTIVE_COUNT_TOO_LOW"


def test_merge_hook_ledger_audit_promotes_rewrite_for_missing_plant_and_resolve() -> None:
    audit = run_hook_ledger_audit([], current_chapter=5)

    merged = merge_hook_ledger_audit_into_chapter_review(
        _passing_review(),
        audit,
        chapter_number=5,
        language="zh-CN",
    )

    assert merged.verdict == "rewrite"
    assert merged.severity_max == "major"
    assert "钩子台账修复" in (merged.rewrite_instructions or "")
    assert "hook_ledger_audit" in merged.evidence_summary
    assert any(f.category == "hook_ledger" for f in merged.findings)


def test_merge_hook_ledger_audit_does_not_rewrite_chapter_one_for_no_resolve_only() -> None:
    audit = run_hook_ledger_audit(
        [
            FakeClue("c1", planted_in_chapter_number=1),
            FakeClue("c2", planted_in_chapter_number=1),
            FakeClue("c3", planted_in_chapter_number=1),
        ],
        current_chapter=1,
    )

    merged = merge_hook_ledger_audit_into_chapter_review(
        _passing_review(),
        audit,
        chapter_number=1,
        language="zh-CN",
    )

    assert merged.verdict == "pass"
    assert merged.rewrite_instructions is None
    assert [f.message.split(":", 1)[0] for f in merged.findings] == [
        "HOOK_PER_CHAPTER_NO_RESOLVE"
    ]
