from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest import mock
from uuid import uuid4

import pytest

from bestseller.domain.review import (
    ChapterReviewResult,
    ChapterReviewScores,
)
from bestseller.services.hook_ledger import run_hook_ledger_audit
from bestseller.services.hook_ledger_runtime import (
    compute_hook_ledger_audit_for_review,
    explicit_hook_contract_for_chapter,
    hook_ledger_audit_to_dict,
    merge_hook_ledger_audit_into_chapter_review,
    render_hook_ledger_planner_contract,
)
from bestseller.services.narrative_obligation_migration import bind_explicit_obligation


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


def _project(mode="off", hook=None, source="explicit"):
    return SimpleNamespace(
        id=uuid4(),
        metadata_json={
            "creation_intent_contract": {
                "field_sources": {"hook_spec": source},
                "hook_spec": {
                    "require_methodology_overlay": True,
                    "methodology_contract_mode": mode,
                    **(hook or {}),
                },
            },
        },
    )


def test_render_hook_ledger_planner_contract_defaults_off() -> None:
    with mock.patch.dict("os.environ", {}, clear=True):
        assert render_hook_ledger_planner_contract(language="zh-CN") == ""


def test_render_hook_ledger_planner_contract_when_enabled() -> None:
    with mock.patch.dict("os.environ", {"BESTSELLER_METHODOLOGY_V2": "1"}):
        block = render_hook_ledger_planner_contract(language="zh-CN", project=_project("strict"))

    assert "方法论 v2 钩子清单合同" in block
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


def test_explicit_strict_suite_promotes_rewrite_for_missing_plant_and_resolve() -> None:
    audit = run_hook_ledger_audit([], current_chapter=5)

    merged = merge_hook_ledger_audit_into_chapter_review(
        _passing_review(),
        audit,
        chapter_number=5,
        language="zh-CN",
        project=_project("strict"),
    )

    assert merged.verdict == "rewrite"
    assert merged.severity_max == "major"
    assert "钩子清单修复" in (merged.rewrite_instructions or "")
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
        project=_project("strict"),
    )

    assert merged.verdict == "pass"
    assert merged.rewrite_instructions is None
    assert [f.message.split(":", 1)[0] for f in merged.findings] == ["HOOK_PER_CHAPTER_NO_RESOLVE"]


@pytest.mark.parametrize(
    "project",
    [
        None,
        _project(),
        _project("invalid"),
        _project("strict", source="derived"),
        _project("strict", source=None),
        SimpleNamespace(metadata_json={"methodology_contract_mode": "strict"}),
        _project("strict", {"require_methodology_overlay": False}),
        _project("strict", {"require_methodology_overlay": {}}),
    ],
)
def test_empty_unselected_ledger_does_not_invent_quotas(project):
    review = _passing_review()
    merged = merge_hook_ledger_audit_into_chapter_review(
        review,
        run_hook_ledger_audit([], current_chapter=5),
        chapter_number=5,
        project=project,
    )
    assert merged.verdict == review.verdict
    assert merged.findings == review.findings
    assert merged.severity_max == review.severity_max
    assert merged.rewrite_instructions == review.rewrite_instructions
    assert merged.evidence_summary["hook_ledger_audit"]["effective_findings"] == []
    assert merged.evidence_summary["hook_ledger_audit"]["findings"]
    with mock.patch.dict("os.environ", {"BESTSELLER_METHODOLOGY_V2": "1"}):
        assert render_hook_ledger_planner_contract(project=project) == ""


@pytest.mark.parametrize(
    "clues,chapter",
    [
        ([], 5),
        ([FakeClue("old", planted_in_chapter_number=1, expected_payoff_by_chapter_number=2)], 20),
        ([FakeClue(str(i), planted_in_chapter_number=1) for i in range(9)], 5),
        ([FakeClue("paid", planted_in_chapter_number=1, actual_paid_off_chapter_number=4)], 5),
    ],
)
def test_warn_is_diagnostic_even_for_real_due_and_overdue(clues, chapter):
    merged = merge_hook_ledger_audit_into_chapter_review(
        _passing_review(),
        run_hook_ledger_audit(clues, current_chapter=chapter),
        chapter_number=chapter,
        project=_project("warn"),
    )
    assert merged.verdict == "pass"
    assert merged.severity_max == "info"
    assert merged.rewrite_instructions is None
    assert merged.findings and all(f.severity == "info" for f in merged.findings)


@pytest.mark.parametrize("due", [None, 2])
def test_raw_old_or_dated_clue_without_source_cannot_grant_due_authority(due):
    audit = run_hook_ledger_audit(
        [FakeClue("unknown", planted_in_chapter_number=1, expected_payoff_by_chapter_number=due)],
        current_chapter=20,
    )
    merged = merge_hook_ledger_audit_into_chapter_review(
        _passing_review(), audit, chapter_number=20
    )
    assert merged.verdict == "pass" and merged.findings == []
    assert any(
        f["code"] == "HOOK_OVERDUE"
        for f in merged.evidence_summary["hook_ledger_audit"]["findings"]
    )
    assert merged.evidence_summary["hook_ledger_audit"]["effective_findings"] == []


@pytest.mark.parametrize(
    "clue",
    [
        FakeClue("future", planted_in_chapter_number=10, expected_payoff_by_chapter_number=2),
        FakeClue("unknown", expected_payoff_by_chapter_number=2),
        FakeClue(
            "cancelled",
            planted_in_chapter_number=1,
            expected_payoff_by_chapter_number=2,
            status="cancelled",
        ),
        FakeClue(
            "paid",
            planted_in_chapter_number=1,
            expected_payoff_by_chapter_number=2,
            actual_paid_off_chapter_number=4,
        ),
    ],
)
def test_off_does_not_invent_due_obligations(clue):
    review = _passing_review()
    assert (
        merge_hook_ledger_audit_into_chapter_review(
            review,
            run_hook_ledger_audit([clue], current_chapter=5),
            chapter_number=5,
        ).findings
        == review.findings
    )


@pytest.mark.parametrize("key", ["stage_contracts", "stages"])
@pytest.mark.parametrize("nested", [False, True])
def test_literal_current_chapter_selection(key, nested):
    literal = {"hooks_to_plant": ["named-hook"], "payoffs_due": ["named-payoff"]}
    stage = {"chapters": {"5": literal}} if nested else {"chapter_number": 5, **literal}
    project = _project(hook={key: {"review": stage}})
    selected = explicit_hook_contract_for_chapter(project, chapter_number=5)
    assert selected["payoffs_due"] == ["named-payoff"]
    assert explicit_hook_contract_for_chapter(project, chapter_number=6) == {}
    assert (
        explicit_hook_contract_for_chapter(project, chapter_number=5, stage="outline_chapter") == {}
    )
    selected["payoffs_due"].append("do-not-mutate-source")
    assert explicit_hook_contract_for_chapter(project, chapter_number=5)["payoffs_due"] == [
        "named-payoff"
    ]


@pytest.mark.parametrize(
    "hook,source",
    [
        ({"hooks_to_plant": ["palette"]}, "explicit"),
        ({"stage_contracts": {"review": {"hooks_to_plant": ["unscoped"]}}}, "explicit"),
        (
            {"stage_contracts": {"review": {"chapter_number": True, "hooks_to_plant": ["bad"]}}},
            "explicit",
        ),
        (
            {"stage_contracts": {"review": {"chapter_number": 5, "hooks_to_plant": ["derived"]}}},
            "derived",
        ),
        ({"stage_contracts": {"review": {"chapters": {"5": {}}}}}, "explicit"),
    ],
)
def test_palette_unknown_empty_and_derived_are_not_chapter_obligations(hook, source):
    project = _project(hook=hook, source=source)
    assert explicit_hook_contract_for_chapter(project, chapter_number=5) == {}
    review = _passing_review()
    assert (
        merge_hook_ledger_audit_into_chapter_review(
            review,
            run_hook_ledger_audit([], current_chapter=5),
            chapter_number=5,
            project=project,
        ).findings
        == review.findings
    )


@pytest.mark.parametrize(
    "key,code",
    [
        ("hooks_to_plant", "HOOK_REQUIRED_PLANT_MISSING"),
        ("hooks_to_resolve", "HOOK_REQUIRED_RESOLVE_MISSING"),
    ],
)
@pytest.mark.parametrize("mode,verdict", [("off", "rewrite"), ("warn", "pass")])
def test_explicit_named_requirements_are_independent_of_suite(key, code, mode, verdict):
    project = _project(
        mode, {"stage_contracts": {"review": {"chapter_number": 5, key: ["specific"]}}}
    )
    merged = merge_hook_ledger_audit_into_chapter_review(
        _passing_review(),
        run_hook_ledger_audit([], current_chapter=5),
        chapter_number=5,
        project=project,
    )
    assert merged.verdict == verdict
    assert any(code in f.message and "specific" in f.message for f in merged.findings)
    assert merged.severity_max == ("major" if verdict == "rewrite" else "info")


def test_explicit_hook_is_satisfied_only_by_its_own_ledger_truth():
    project = _project(
        hook={
            "stage_contracts": {
                "review": {
                    "chapter_number": 5,
                    "hooks_to_plant": ["specific"],
                    "hooks_to_resolve": ["old"],
                }
            }
        }
    )
    review = _passing_review()
    audit = run_hook_ledger_audit(
        [
            FakeClue("specific", planted_in_chapter_number=5),
            FakeClue("old", planted_in_chapter_number=1, actual_paid_off_chapter_number=5),
        ],
        current_chapter=5,
    )
    assert (
        merge_hook_ledger_audit_into_chapter_review(
            review, audit, chapter_number=5, project=project
        ).findings
        == review.findings
    )


@pytest.mark.asyncio
async def test_compute_does_not_turn_planned_contract_delta_into_accepted_events():
    session = SimpleNamespace(scalars=mock.AsyncMock(return_value=[]))
    with mock.patch.dict("os.environ", {"BESTSELLER_METHODOLOGY_V2": "1"}):
        audit = await compute_hook_ledger_audit_for_review(
            session=session,
            project=_project(),
            chapter=SimpleNamespace(chapter_number=5),
            chapter_contract=SimpleNamespace(
                hooks_to_plant=["planned"], hooks_to_resolve=["planned"]
            ),
        )
    assert audit.view == ()
    assert audit.per_chapter_balance.plant_count == audit.per_chapter_balance.resolve_count == 0


def test_existing_review_failure_is_not_erased_by_warn():
    review = _passing_review().model_copy(
        update={
            "verdict": "rewrite",
            "severity_max": "critical",
            "rewrite_instructions": "existing repair",
        }
    )
    merged = merge_hook_ledger_audit_into_chapter_review(
        review,
        run_hook_ledger_audit([], current_chapter=5),
        chapter_number=5,
        project=_project("warn"),
    )
    assert merged.verdict == "rewrite" and merged.severity_max == "critical"
    assert merged.rewrite_instructions == "existing repair"
    assert all(f.severity == "info" for f in merged.findings)


@pytest.mark.parametrize("values", [[], [None, False, 1, {}], ["", " "]])
def test_empty_or_invalid_literal_hook_values_do_not_invent_items(values):
    project = _project(
        hook={
            "stage_contracts": {
                "review": {
                    "chapter_number": 5,
                    "hooks_to_plant": values,
                    "hooks_to_resolve": values,
                }
            }
        }
    )
    merged = merge_hook_ledger_audit_into_chapter_review(
        _passing_review(),
        run_hook_ledger_audit([], current_chapter=5),
        chapter_number=5,
        project=project,
    )
    assert merged.verdict == "pass" and merged.findings == []
    assert merged.evidence_summary["hook_ledger_audit"]["findings"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source", ["approved", "unknown", "user", "tampered", "changed_due", "foreign"]
)
@pytest.mark.parametrize("mode", ["off", "warn"])
async def test_only_source_bound_due_survives_compute_and_merge(source, mode):
    project = _project(mode)
    record = {"label": "specific promise", "due_chapter": 5}
    artifact = {
        "id": "approved-outline",
        "project_id": str(project.id),
        "status": "approved",
        "artifact_type": "chapter_outline_batch",
        "content": {"hook": record},
    }
    marker = bind_explicit_obligation(artifact, ["hook"])
    metadata = {"narrative_obligation": marker}
    if source == "unknown":
        metadata = {}
    elif source == "user":
        metadata = {"source": "user", "due_chapter": 5}
    elif source == "tampered":
        marker["source"]["record"]["label"] = "forged"
    elif source == "foreign":
        marker["source"]["project_id"] = "other-project"
    clue = FakeClue(
        "real",
        label="specific promise",
        planted_in_chapter_number=1,
        expected_payoff_by_chapter_number=6 if source == "changed_due" else 5,
        metadata_json=metadata,
    )
    session = SimpleNamespace(scalars=mock.AsyncMock(return_value=[clue]))
    with mock.patch.dict("os.environ", {"BESTSELLER_METHODOLOGY_V2": "1"}):
        audit = await compute_hook_ledger_audit_for_review(
            session=session,
            project=project,
            chapter=SimpleNamespace(chapter_number=20),
            chapter_contract=None,
        )
    merged = merge_hook_ledger_audit_into_chapter_review(
        _passing_review(),
        audit,
        chapter_number=20,
        project=project,
    )
    if source == "approved":
        assert any("HOOK_DUE_UNRESOLVED" in f.message for f in merged.findings)
        assert (
            merged.evidence_summary["hook_ledger_audit"]["explicit_due"]["real"]["due_chapter"] == 5
        )
        assert merged.verdict == ("rewrite" if mode == "off" else "pass")
        assert merged.severity_max == ("major" if mode == "off" else "info")
        if mode == "off":
            assert "real" in merged.rewrite_instructions
            assert "HOOK_PER_CHAPTER" not in merged.rewrite_instructions
    else:
        assert not any("HOOK_DUE_UNRESOLVED" in f.message for f in merged.findings)
        assert merged.verdict == "pass"
    assert not any("HOOK_OVERDUE" in f.message for f in merged.findings)
    assert any(
        f["code"] == "HOOK_OVERDUE"
        for f in merged.evidence_summary["hook_ledger_audit"]["findings"]
    )


@pytest.mark.parametrize(
    "clues", [[], [FakeClue("paid", planted_in_chapter_number=1, actual_paid_off_chapter_number=4)]]
)
def test_explicit_empty_chapter_hooks_override_strict_generic_quotas(clues):
    project = _project(
        "strict",
        {
            "stage_contracts": {
                "review": {
                    "chapter_number": 5,
                    "hooks_to_plant": [],
                    "hooks_to_resolve": [],
                }
            }
        },
    )
    merged = merge_hook_ledger_audit_into_chapter_review(
        _passing_review(),
        run_hook_ledger_audit(clues, current_chapter=5),
        chapter_number=5,
        project=project,
    )
    assert merged.verdict == "pass"
    assert merged.findings == []
    assert merged.evidence_summary["hook_ledger_audit"]["effective_findings"] == []
    raw = merged.evidence_summary["hook_ledger_audit"]["findings"]
    assert any(f["code"] == "HOOK_PER_CHAPTER_NO_PLANT" for f in raw)
    assert any(f["code"] == "HOOK_PER_CHAPTER_NO_RESOLVE" for f in raw)
