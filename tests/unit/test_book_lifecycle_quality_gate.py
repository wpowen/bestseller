from __future__ import annotations

import json

import pytest

from bestseller.services.book_lifecycle_quality_gate import (
    build_lifecycle_quality_report_from_closure,
    evaluate_book_lifecycle_quality,
)

pytestmark = pytest.mark.unit


def _ready_inputs() -> dict[str, object]:
    return {
        "slug": "book-a",
        "planning_report": {
            "category": "suspense-mystery",
            "target_chapters": 100,
            "planned_chapters": 100,
            "prewrite_readiness_report": {"passed": True},
            "reverse_outline_gate_report": {"passed": True},
            "story_design_kernel": {"valid": True},
        },
        "character_report": {
            "bible_gate_report": {"passed": True},
            "identity_manifest": [{"name": "主角", "locked": True}],
            "identity_registry_coverage": 1.0,
            "personhood_coverage": 1.0,
        },
        "chapter_report": {
            "target_chapters": 100,
            "current_chapters": 100,
            "scorecard": {
                "quality_score": 84.0,
                "missing_chapters": 0,
                "draftless_chapters": 0,
                "chapters_blocked": 0,
                "length_cv": 0.08,
            },
            "repair_plan": {"task_count": 0},
        },
        "whole_book_report": {
            "target_chapters": 100,
            "current_chapters": 100,
            "acceptance": {"passed": True},
            "premium_gate": {"passed": True},
            "model_preflight": {"ready": True, "provider": "deepseek"},
            "rewrite_generation_audit": {"invalid": 0, "gate_rejected": 0},
            "chapter_generation_audit": {"invalid": 0, "gate_rejected": 0},
        },
        "anti_copy_report": {
            "reference_distance_score": 0.81,
            "source_leak_count": 0,
            "protected_phrase_leak_count": 0,
        },
    }


def test_lifecycle_gate_passes_complete_ready_evidence() -> None:
    report = evaluate_book_lifecycle_quality(**_ready_inputs())  # type: ignore[arg-type]

    assert report.passed is True
    assert report.readiness_level == "ready"
    assert report.findings == ()
    assert {status.domain for status in report.domain_statuses} == {
        "planning",
        "character",
        "chapter_execution",
        "whole_book",
        "anti_copy",
    }


def test_lifecycle_gate_blocks_current_historical_incomplete_shape() -> None:
    closure = {
        "slug": "exorcist-detective-1778051012",
        "model_preflight": {"ready": True, "provider": "deepseek"},
        "rewrite_generation_audit": {"invalid": 0, "gate_rejected": 1},
        "chapter_generation_audit": {"invalid": 0, "gate_rejected": 0},
        "continuation_plan": {
            "target_chapters": 500,
            "planned_chapters": 100,
            "current_chapters": 64,
            "draftless_planned_chapters": 36,
            "unplanned_chapters": 400,
        },
        "after_acceptance": {
            "slug": "exorcist-detective-1778051012",
            "category": "suspense-mystery",
            "target_chapters": 500,
            "current_chapters": 64,
            "acceptance": {"passed": False, "readiness_level": "blocked"},
            "premium_gate": {"passed": True},
            "scorecard": {
                "total_chapters": 100,
                "current_chapters": 64,
                "quality_score": 61.06,
                "missing_chapters": 400,
                "draftless_chapters": 36,
                "chapters_blocked": 0,
                "length_cv": 0.1193,
            },
            "repair_plan": {"task_count": 0},
        },
    }

    report = build_lifecycle_quality_report_from_closure(closure)
    codes = {finding.code for finding in report.findings}

    assert report.passed is False
    assert report.readiness_level == "blocked"
    assert {
        "planning_outline_below_target",
        "reverse_outline_gate_not_passed",
        "planning_kernel_not_verified",
        "prewrite_readiness_not_passed",
        "character_gate_evidence_missing",
        "identity_manifest_missing",
        "scorecard_below_lifecycle_bar",
        "book_incomplete",
        "planned_chapters_without_current_drafts",
        "length_stability_below_bar",
        "whole_book_acceptance_not_passed",
        "reference_distance_missing",
    }.issubset(codes)


def test_lifecycle_gate_uses_collected_lifecycle_evidence() -> None:
    closure = {
        "slug": "book-a",
        "model_preflight": {"ready": True, "provider": "deepseek"},
        "lifecycle_evidence": {
            "planning_report": {
                "target_chapters": 100,
                "planned_chapters": 100,
                "prewrite_readiness_report": {"passed": True},
                "reverse_outline_gate_report": {"passed": True},
                "planning_kernel": {
                    "story_design": {"valid": True},
                    "emotion_driven": {"valid": True},
                },
            },
            "character_report": {
                "character_gate_report": {"passed": True},
                "identity_manifest": [{"name": "主角"}],
                "identity_registry_coverage": 1.0,
                "personhood_coverage": 1.0,
            },
            "anti_copy_report": {"reference_distance_score": 0.82},
        },
        "after_acceptance": {
            "slug": "book-a",
            "category": "suspense-mystery",
            "target_chapters": 100,
            "current_chapters": 100,
            "acceptance": {"passed": True},
            "premium_gate": {"passed": True},
            "scorecard": {
                "total_chapters": 10,
                "current_chapters": 100,
                "quality_score": 86.0,
                "missing_chapters": 0,
                "draftless_chapters": 0,
                "chapters_blocked": 0,
                "length_cv": 0.07,
            },
            "repair_plan": {"task_count": 0},
        },
    }

    report = build_lifecycle_quality_report_from_closure(closure)

    assert report.passed is True
    assert report.metrics["planned_chapters"] == 100


def test_lifecycle_gate_blocks_fallback_generation_evidence() -> None:
    inputs = _ready_inputs()
    whole_book = dict(inputs["whole_book_report"])  # type: ignore[arg-type]
    whole_book["chapter_generation_audit"] = {
        "invalid": 1,
        "gate_rejected": 0,
        "invalid_generation_modes": ["fallback"],
    }
    inputs["whole_book_report"] = whole_book

    report = evaluate_book_lifecycle_quality(**inputs)  # type: ignore[arg-type]
    codes = {finding.code for finding in report.findings}

    assert report.passed is False
    assert report.readiness_level == "blocked"
    assert "invalid_generation_mode_detected" in codes
    assert "invalid_generation_modes_present" in codes


def test_lifecycle_report_to_dict_is_json_safe() -> None:
    report = evaluate_book_lifecycle_quality(**_ready_inputs())  # type: ignore[arg-type]

    payload = report.to_dict()

    assert json.loads(json.dumps(payload))["passed"] is True
