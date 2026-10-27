from __future__ import annotations

import json
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ProjectModel
from bestseller.services.book_creation_readiness_gate import (
    evaluate_book_creation_readiness,
)
from bestseller.services.character_intelligence.strategy import adopt_reference_projection
from bestseller.services.planning_capability_selection import CAPABILITIES, CONTRACT_SCHEMA
from bestseller.services.prompt_source_policy import stamp_reference_cache

pytestmark = pytest.mark.unit


def _ready_metadata() -> dict[str, object]:
    return {
        "creation_intent_contract": {
            "field_sources": {"hook_spec": "explicit"},
            "hook_spec": {"planning_capabilities": {
                "schema": CONTRACT_SCHEMA,
                "selected": {key: key == "story_design_kernel" for key in CAPABILITIES},
            }},
        },
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


def _project(metadata: dict[str, object]) -> ProjectModel:
    return ProjectModel(
        id=uuid4(), slug="demo", title="Readiness Test", genre="悬疑", sub_genre=None,
        language="zh-CN", audience="general", target_chapters=120,
        target_word_count=300000, reader_contract_json={"core_fantasy": "证据推动选择"},
        dramatic_question="谁隐瞒了证据？", theme_statement="信任与真相",
        metadata_json=metadata,
    )


def _evaluate(project: ProjectModel):
    return evaluate_book_creation_readiness(
        project_slug=project.slug, project_metadata=project.metadata_json, project=project,
        target_chapters=120, planned_chapters=120, story_design_kernel={"valid": True},
        volume_plan_report={"passed": True}, prewrite_report={"passed": True},
        forward_state_report={"passed": True}, reveal_alignment_report={"passed": True},
    )


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
    assert "story_design_grammar_key_missing" not in codes
    assert report.domain_statuses[1].metrics["story_design_kernel_valid"] is None
    assert report.metrics["planning_capabilities"]["story_design_kernel"]["status"] == "not_applicable"
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

    project = _project(metadata)
    stamp_reference_cache(project, ("distilled_strategy_card",), producer="strategy_compiler")
    adopt_reference_projection(project, "distilled_strategy_card", adopted_by="fixture-author", required=True)
    report = _evaluate(project)

    assert report.passed is False
    assert "distilled_strategy_card_unsafe" in {
        finding.code for finding in report.findings
    }


@pytest.mark.parametrize("reference_state", ["absent", "raw", "verified_optional"])
def test_optional_reference_does_not_block_creation_readiness(reference_state: str) -> None:
    metadata = _ready_metadata()
    if reference_state == "absent":
        metadata.pop("distilled_strategy_card")
    else:
        metadata["distilled_strategy_card"] = {
            "aggregate_key": "suspense-mystery", "maturity_status": "unsafe", "maturity_score": 0.12,
        }
    project = _project(metadata)
    if reference_state == "verified_optional":
        stamp_reference_cache(project, ("distilled_strategy_card",), producer="strategy_compiler")
        adopt_reference_projection(project, "distilled_strategy_card", adopted_by="fixture-author", required=False)

    report = _evaluate(project)

    assert report.passed is True
    assert report.findings == ()
    assert report.aggregate_gate_report.verdict == "pass"


@pytest.mark.parametrize("context", ["missing_receipt", "missing_project"])
def test_required_reference_cannot_pass_without_verifiable_context(context: str) -> None:
    project = _project(_ready_metadata())
    stamp_reference_cache(project, ("distilled_strategy_card",), producer="strategy_compiler")
    adopt_reference_projection(project, "distilled_strategy_card", adopted_by="fixture-author", required=True)
    if context == "missing_receipt":
        project.metadata_json["prompt_reference_receipts"].pop("distilled_strategy_card")
        report = _evaluate(project)
    else:
        report = evaluate_book_creation_readiness(
            project_slug=project.slug, project_metadata=project.metadata_json,
            target_chapters=120, planned_chapters=120, story_design_kernel={"valid": True},
            volume_plan_report={"passed": True}, prewrite_report={"passed": True},
            forward_state_report={"passed": True}, reveal_alignment_report={"passed": True},
        )
    assert report.passed is False
    expected = "adopted_reference_unavailable" if context == "missing_receipt" else "reference_context_required"
    assert expected in {finding.code for finding in report.findings}
