from __future__ import annotations

import json

import pytest

from bestseller.services.book_creation_readiness_gate import (
    evaluate_book_creation_readiness,
)

pytestmark = pytest.mark.unit


def _ready_metadata() -> dict[str, object]:
    return {
        "methodology_contract_mode": "strict",
        "category_key": "suspense-mystery",
        "story_design_grammar_key": "suspense-mystery",
        "distilled_strategy_expected": True,
        "distilled_strategy_card": {
            "aggregate_key": "suspense-mystery",
            "maturity_status": "production",
            "maturity_score": 0.86,
            "required_state_variables": ["现实证据链完整度"],
        },
    }


def test_strict_mode_does_not_require_optional_distillation_reference() -> None:
    report = evaluate_book_creation_readiness(
        project_slug="demo",
        project_metadata={"methodology_contract_mode": "strict"},
        target_chapters=120,
        planned_chapters=120,
        story_design_kernel={"valid": True},
        volume_plan_report={"passed": True},
        prewrite_report={"passed": True},
        forward_state_report={"passed": True},
        reveal_alignment_report={"passed": True},
    )

    codes = {finding.code for finding in report.findings}
    assert report.passed is False
    assert report.readiness_level == "blocked"
    assert report.aggregate_gate_report.verdict == "blocked"
    assert "category_key_missing" in codes
    assert "story_design_grammar_key_missing" in codes
    assert "distilled_strategy_card_missing" not in codes


def test_passes_when_all_lifecycle_assets_are_ready() -> None:
    report = evaluate_book_creation_readiness(
        project_slug="demo",
        project_metadata=_ready_metadata(),
        target_chapters=120,
        planned_chapters=120,
        story_design_kernel={"valid": True},
        volume_plan_report={"passed": True},
        prewrite_report={"passed": True},
        forward_state_report={"passed": True},
        reveal_alignment_report={"passed": True},
    )

    assert report.passed is True
    assert report.readiness_level == "ready"
    assert report.aggregate_gate_report.verdict == "pass"
    assert report.findings == ()
    assert json.loads(json.dumps(report.to_dict()))["passed"] is True


def test_blocks_failed_downstream_asset_gate() -> None:
    report = evaluate_book_creation_readiness(
        project_slug="demo",
        project_metadata=_ready_metadata(),
        target_chapters=120,
        planned_chapters=120,
        story_design_kernel={"valid": True},
        volume_plan_report={"passed": True},
        prewrite_report={
            "passed": False,
            "gate_name": "prewrite_contract_readiness",
            "findings": [
                {
                    "code": "PREWRITE_PLACEHOLDER_TEXT",
                    "severity": "critical",
                    "message": "placeholder",
                    "path": "prewrite-contract.json:chapter:83",
                }
            ],
        },
        forward_state_report={"passed": True},
        reveal_alignment_report={"passed": True},
    )

    codes = {finding.code for finding in report.findings}
    assert report.passed is False
    assert "prewrite_gate_not_passed" in codes


def test_blocks_unsafe_distilled_strategy_card() -> None:
    metadata = _ready_metadata()
    metadata["distilled_strategy_card"] = {
        "aggregate_key": "suspense-mystery",
        "maturity_status": "unsafe",
        "maturity_score": 0.12,
    }

    report = evaluate_book_creation_readiness(
        project_slug="demo",
        project_metadata=metadata,
        target_chapters=120,
        planned_chapters=120,
        story_design_kernel={"valid": True},
        volume_plan_report={"passed": True},
        prewrite_report={"passed": True},
        forward_state_report={"passed": True},
        reveal_alignment_report={"passed": True},
    )

    assert "distilled_strategy_card_unsafe" in {
        finding.code for finding in report.findings
    }
